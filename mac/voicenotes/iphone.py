"""iPhone importer: feeds recordings synced over iCloud into the inbox, like the receiver does.

A stand-in for the keychain while the hardware is being built. Record on the iPhone (Voice
Memos, or a Shortcut that saves to iCloud Drive); iCloud syncs the file to the Mac; this
converts it to the 16 kHz mono WAV the processor expects and writes the usual inbox pair
(``docs/PROTOCOL.md`` section 4). The processor does not know the difference.

Source files are only read, never moved or deleted. Run with::

    python -m voicenotes.iphone                 # import once (what launchd runs)
    python -m voicenotes.iphone --since 2026-09-01   # also import older recordings
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import Paths, load_config, resolve, setup_logging
from .processor import single_instance
from .receiver import CHUNK, Index, unique_name, write_json_atomic

AUDIO_EXTENSIONS = {".m4a", ".qta", ".caf", ".aac", ".mp3", ".wav", ".mov"}
MAX_ATTEMPTS = 3
# Voice Memos: "20260926 154210-1A2B3C4D.m4a". Shortcut: "2026-09-26_15-42-10.m4a". Both local time.
FILENAME_TIMES = [(re.compile(r"(\d{8} \d{6})"), "%Y%m%d %H%M%S"),
                  (re.compile(r"(\d{4}-\d{2}-\d{2}[_ T]\d{2}[-.:]\d{2}[-.:]\d{2})"), None)]

log = logging.getLogger("iphone")

Probe = Callable[[Path], "datetime | None"]
Convert = Callable[[Path, Path], None]


def time_from_filename(name: str) -> datetime | None:
    """Parse a local recording time out of a Voice Memos or Shortcut file name."""
    for pattern, fmt in FILENAME_TIMES:
        m = pattern.search(name)
        if not m:
            continue
        text = m.group(1)
        try:
            if fmt:
                t = datetime.strptime(text, fmt)
            else:
                t = datetime.strptime(re.sub(r"[-.:_ T]", "", text), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        return t.astimezone()  # naive -> the Mac's local zone
    return None


def ffprobe_creation_time(path: Path) -> datetime | None:
    """The ``creation_time`` tag iPhone recordings carry (UTC), or ``None``.

    Raises
    ------
    RuntimeError
        If ffprobe cannot read the file at all (not audio, or not fully synced yet).
    """
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
                          "-of", "json", str(path)], capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()}")
    tag = json.loads(out.stdout or "{}").get("format", {}).get("tags", {}).get("creation_time")
    try:
        return datetime.fromisoformat(tag.replace("Z", "+00:00")) if tag else None
    except ValueError:
        return None


def ffmpeg_to_wav(src: Path, dst: Path) -> None:
    """Convert any audio file to a 16 kHz, 16-bit mono WAV (the keychain's format)."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-vn",
                          "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-f", "wav", str(dst)],
                         capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"ffmpeg failed: {out.stderr.strip()}")


def sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            sha.update(chunk)
    return sha.hexdigest()


def wav_duration(path: Path) -> float:
    with wave.open(str(path)) as w:
        return round(w.getnframes() / w.getframerate(), 2)


class State:
    """What the importer has already looked at, persisted in ``.state/iphone.json``.

    ``seen`` maps a source path to ``[size, mtime]``, so unchanged files are skipped
    without hashing them. ``since`` is the cut-off for recordings to import.
    """

    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text()) if path.exists() else {}
        self.since: str | None = data.get("since")
        self.seen: dict[str, list] = data.get("seen", {})
        self.failures: dict[str, int] = data.get("failures", {})

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"since": self.since, "seen": self.seen, "failures": self.failures}, indent=2))
        os.replace(tmp, self.path)


class Importer:
    """Imports new recordings from the watched folders into the inbox.

    Parameters
    ----------
    paths : Paths
        File layout. Folders are created if missing.
    watch_dirs : list of Path
        Folders iCloud syncs the iPhone recordings into. Missing folders are skipped.
    device_id : str, optional
        ``device_id`` written to the sidecar.
    since : datetime, optional
        Ignore recordings made before this. Default: the time of the first run, so an
        existing Voice Memos library is not imported wholesale.
    settle_seconds : float, optional
        Skip files modified more recently than this (still recording or syncing).
    probe, convert : callable, optional
        Replace ffprobe / ffmpeg (for tests).
    """

    def __init__(self, paths: Paths, watch_dirs: list[Path], device_id: str = "iphone",
                 since: datetime | None = None, settle_seconds: float = 30,
                 probe: Probe = ffprobe_creation_time, convert: Convert = ffmpeg_to_wav):
        self.paths = paths.ensure()
        self.watch_dirs = watch_dirs
        self.device_id = device_id
        self.settle_seconds = settle_seconds
        self.probe, self.convert = probe, convert
        self._index = Index(paths.state / "received.sqlite")
        self.state = State(paths.state / "iphone.json")
        if since:
            self.since = since
        else:
            if not self.state.since:
                self.state.since = datetime.now(timezone.utc).isoformat(timespec="seconds")
                log.info("First run: importing recordings made from now on (use --since for older ones)")
            self.since = datetime.fromisoformat(self.state.since)

    def candidates(self) -> list[Path]:
        """Audio files in the watched folders, oldest first. Asks iCloud to download placeholders."""
        files = []
        for d in self.watch_dirs:
            if not d.is_dir():
                log.warning("Watched folder %s does not exist (or no permission to read it)", d)
                continue
            for p in d.iterdir():
                if p.name.startswith(".") and p.name.endswith(".icloud"):
                    subprocess.run(["brctl", "download", str(p)], capture_output=True)  # best effort
                elif p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
                    files.append(p)
        return sorted(files, key=lambda p: p.stat().st_mtime)

    def run_once(self) -> int:
        """Import every new, settled recording. Returns how many went into the inbox."""
        imported = 0
        try:
            for src in self.candidates():
                st = src.stat()
                key, sig = str(src), [st.st_size, int(st.st_mtime)]
                if self.state.seen.get(key) == sig or time.time() - st.st_mtime < self.settle_seconds:
                    continue
                try:
                    imported += self.import_file(src)
                except Exception as e:
                    n = self.state.failures[key] = self.state.failures.get(key, 0) + 1
                    log.warning("Could not import %s (attempt %d/%d): %s", src.name, n, MAX_ATTEMPTS, e)
                    if n < MAX_ATTEMPTS:
                        continue
                    log.error("Giving up on %s", src.name)
                self.state.failures.pop(key, None)
                self.state.seen[key] = sig
        finally:
            self.state.save()
        return imported

    def import_file(self, src: Path) -> bool:
        """Convert one recording and write it to the inbox. ``False`` if skipped (old or duplicate)."""
        received = datetime.now(timezone.utc).astimezone()
        recorded, source = self.probe(src) or time_from_filename(src.name), "device_clock"
        if recorded is None:  # file time: when it was saved or synced, so shown as approximate
            recorded, source = datetime.fromtimestamp(src.stat().st_mtime, timezone.utc), "received"
        recorded = recorded.astimezone()
        if recorded < self.since:
            return False
        source_sha = sha256_file(src)
        if name := self._index.find(self.device_id, 0, source_sha):
            log.info("%s was already imported as %s", src.name, name)
            return False

        part = self.paths.incoming / f"{uuid.uuid4().hex}.wav"
        try:
            self.convert(src, part)
            name = unique_name(self._index, self.paths.inbox, f"{recorded:%Y-%m-%d_%H-%M-%S}")
            duration = wav_duration(part)
            sidecar = {"schema": 1, "device_id": self.device_id, "fw_version": "", "seq": 0, "boot_id": "",
                       "time_synced": source == "device_clock", "started_at": int(recorded.timestamp()),
                       "started_mono": 0, "duration_s": duration, "sample_rate": 16000, "bits_per_sample": 16,
                       "channels": 1, "stop_reason": "button_off", "battery_v": None, "dropped_ms": 0,
                       "recorded_at": recorded.isoformat(timespec="seconds"), "time_source": source,
                       "received_at": received.isoformat(timespec="seconds"), "sha256": sha256_file(part),
                       "original_filename": src.name, "source_sha256": source_sha}
            os.replace(part, self.paths.inbox / f"{name}.wav")
            write_json_atomic(self.paths.inbox / f"{name}.json", sidecar, self.paths.incoming)
            self._index.add(self.device_id, 0, source_sha, name, sidecar["received_at"])
        finally:
            part.unlink(missing_ok=True)
        log.info("Imported %s as %s (%.1fs)", src.name, name, duration)
        return True


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description="Import iPhone recordings synced over iCloud into the inbox.")
    ap.add_argument("--config", help="config file (default: <repo>/config.yaml)")
    ap.add_argument("--since", type=datetime.fromisoformat,
                    help="import recordings made on or after this date (YYYY-MM-DD), e.g. to backfill")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    paths = Paths.from_config(cfg).ensure()
    setup_logging(paths.log)
    ic = cfg.get("iphone") or {}
    dirs = [resolve(d, Path.home()) for d in ic.get("watch_dirs") or []]
    if not dirs:
        log.error("No iphone.watch_dirs in config.yaml; nothing to import")
        return
    since = args.since.astimezone() if args.since else None

    with single_instance(paths.state / "iphone.lock") as ok:
        if not ok:
            log.info("Another import is running; exiting")
            return
        importer = Importer(paths, dirs, ic.get("device_id", "iphone"), since, ic.get("settle_seconds", 30))
        if since:  # a backfill: look at files already skipped as too old
            importer.state.seen.clear()
        importer.run_once()


if __name__ == "__main__":
    main()
