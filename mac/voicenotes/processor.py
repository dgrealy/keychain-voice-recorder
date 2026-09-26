"""Processor: turns notes in the inbox into ``notes.md`` entries.

For every ``<stem>.json`` (and its ``<stem>.wav``) in the inbox:

1. transcribe with Whisper (the transcript is saved first, so a retry skips this step)
2. structure with Ollama into a title and cleaned-up content
3. append the entry to ``notes.md``
4. move the audio and sidecar to ``archive/audio/YYYY-MM/``

Usage::

    python -m voicenotes.processor                 # process the inbox once (what launchd runs)
    python -m voicenotes.processor --watch         # keep polling
    python -m voicenotes.processor --fake-models   # dry run without Whisper/Ollama
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Protocol

from . import notes
from .agent_calls import Response, Transcriber, Translator
from .config import REPO_ROOT, Paths, load_config, resolve, setup_logging

log = logging.getLogger("processor")
NO_SPEECH = Response(title="(no speech detected)", content="_(no speech detected)_")


class SpeechToText(Protocol):
    """Anything with ``transcribe(audio_path) -> str`` (e.g. :class:`Transcriber`)."""

    def transcribe(self, audio_path: Path) -> str: ...


class Structurer(Protocol):
    """Anything with ``translate(transcript) -> Response`` (e.g. :class:`Translator`)."""

    def translate(self, transcript: str) -> Response: ...


class FakeTranscriber:
    """Stand-in for Whisper, used with ``--fake-models``."""

    def transcribe(self, audio_path: Path) -> str:
        return f"Fake transcript of {audio_path.name}."


class FakeTranslator:
    """Stand-in for Ollama, used with ``--fake-models``."""

    def translate(self, transcript: str) -> Response:
        return Response(title="fake title", content=transcript)


def build_models(cfg: dict) -> tuple[Transcriber, Translator]:
    """Create the real models from the ``whisper`` and ``ollama`` config sections."""
    w, o = cfg["whisper"], cfg["ollama"]
    return (Transcriber(w["model"], w.get("language", "en")),
            Translator(o["model"], resolve(o["system_prompt"], REPO_ROOT),
                       resolve(o["examples"], REPO_ROOT), o.get("options")))


def process_note(meta_path: Path, paths: Paths, transcriber: SpeechToText, translator: Structurer) -> bool:
    """Process one inbox note end to end. Every step is safe to repeat after a crash.

    Parameters
    ----------
    meta_path : Path
        The note's ``<stem>.json`` in the inbox.
    paths : Paths
        File layout.
    transcriber, translator
        Speech-to-text and structuring models.

    Returns
    -------
    bool
        ``True`` if a new entry was added to ``notes.md`` (``False`` if it was already there).
    """
    stem, wav = meta_path.stem, meta_path.with_suffix(".wav")
    meta = json.loads(meta_path.read_text())
    recorded = datetime.fromisoformat(meta["recorded_at"])
    archive = paths.audio_archive / f"{recorded:%Y-%m}"
    transcript_path = paths.transcripts / f"{stem}.txt"

    added = False
    if not notes.has_entry(paths.notes, stem):
        if transcript_path.exists():
            transcript = Transcriber.read_transcript(transcript_path)
        else:
            transcript = transcriber.transcribe(wav)
            Transcriber.write_transcript(transcript, transcript_path)
        result = translator.translate(transcript) if transcript.strip() else NO_SPEECH
        entry = notes.format_entry(stem, recorded, result.title, result.content, archive / wav.name,
                                   transcript_path, approximate=meta.get("time_source") == "received")
        added = notes.append_entry(paths.notes, stem, entry)
        log.info("Added %s to %s: %s", stem, paths.notes.name, result.title)

    archive.mkdir(parents=True, exist_ok=True)
    for f in (wav, meta_path):  # JSON last: while it is in the inbox, the note counts as pending
        if f.exists():
            f.replace(archive / f.name)
    return added


class Attempts:
    """Failure counts per note, persisted in ``.state/attempts.json``.

    Parameters
    ----------
    path : Path
        JSON file (created on first save).
    """

    def __init__(self, path: Path):
        self.path = path
        self.counts: dict[str, int] = json.loads(path.read_text()) if path.exists() else {}

    def fail(self, stem: str) -> int:
        """Record a failure and return the new count."""
        self.counts[stem] = self.counts.get(stem, 0) + 1
        return self.counts[stem]

    def clear(self, stem: str) -> None:
        self.counts.pop(stem, None)

    def save(self) -> None:
        self.path.write_text(json.dumps(self.counts, indent=2))


def move_to_failed(meta_path: Path, paths: Paths, error: str) -> None:
    """Move a note that keeps failing to ``failed/`` with an ``.error.txt`` beside it."""
    for f in (meta_path.with_suffix(".wav"), meta_path):
        if f.exists():
            f.replace(paths.failed / f.name)
    (paths.failed / f"{meta_path.stem}.error.txt").write_text(error)


def run_once(paths: Paths, transcriber: SpeechToText, translator: Structurer, max_attempts: int = 3) -> int:
    """Process every complete note in the inbox, oldest first.

    A note that raises is left in the inbox and retried on the next run; after
    ``max_attempts`` failures it is moved to ``failed/``. If a model is unavailable
    (Ollama not running, package not installed) the run stops without counting a failure.

    Returns
    -------
    int
        Number of entries added to ``notes.md``.
    """
    attempts = Attempts(paths.state / "attempts.json")
    added = 0
    for meta_path in sorted(paths.inbox.glob("*.json")):
        stem = meta_path.stem
        try:
            added += process_note(meta_path, paths, transcriber, translator)
            attempts.clear(stem)
        except (ConnectionError, ImportError) as e:
            log.error("Model unavailable, will retry later: %s", e)
            break
        except Exception:
            n = attempts.fail(stem)
            log.exception("Failed to process %s (attempt %d/%d)", stem, n, max_attempts)
            if n >= max_attempts:
                move_to_failed(meta_path, paths, traceback.format_exc())
                attempts.clear(stem)
                log.error("Moved %s to failed/", stem)
    attempts.save()
    return added


@contextmanager
def single_instance(lock_path: Path) -> Iterator[bool]:
    """Yield ``True`` if this process holds the lock, ``False`` if another run already does."""
    with lock_path.open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description="Turn inbox voice notes into notes.md entries.")
    ap.add_argument("--config", help="config file (default: <repo>/config.yaml)")
    ap.add_argument("--watch", action="store_true", help="keep polling the inbox")
    ap.add_argument("--fake-models", action="store_true", help="skip Whisper/Ollama (for testing)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    paths = Paths.from_config(cfg).ensure()
    setup_logging(paths.log)
    models = (FakeTranscriber(), FakeTranslator()) if args.fake_models else build_models(cfg)
    mac = cfg.get("mac", {})

    with single_instance(paths.state / "processor.lock") as ok:
        if not ok:
            log.info("Another processor run is active; exiting")
            return
        while True:
            run_once(paths, *models, max_attempts=mac.get("max_attempts", 3))
            if not args.watch:
                break
            time.sleep(mac.get("poll_seconds", 30))


if __name__ == "__main__":
    main()
