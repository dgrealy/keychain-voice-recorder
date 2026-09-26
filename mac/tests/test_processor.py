import json

from voicenotes import processor
from voicenotes.agent_calls import Response, Translator
from voicenotes.config import REPO_ROOT
from voicenotes.fake_device import make_wav


class RecordingModels:
    """Fake transcriber + translator that count calls and can be made to fail."""

    def __init__(self, transcript="hello there", error=None):
        self.transcript, self.error = transcript, error
        self.transcribed, self.translated = [], []

    def transcribe(self, audio_path):
        self.transcribed.append(audio_path.name)
        return self.transcript

    def translate(self, transcript):
        self.translated.append(transcript)
        if self.error:
            raise self.error
        return Response(title="A title", content=f"Cleaned: {transcript}")


def add_note(paths, stem="2026-09-16_17-56-00", time_source="device_clock"):
    (paths.inbox / f"{stem}.wav").write_bytes(make_wav(0.1))
    meta = {"recorded_at": "2026-09-16T17:56:00+01:00", "time_source": time_source, "duration_s": 0.1}
    (paths.inbox / f"{stem}.json").write_text(json.dumps(meta))
    return stem


def run(paths, models, **kw):
    return processor.run_once(paths, models, models, **kw)


def test_note_is_added_and_archived(paths):
    stem, models = add_note(paths), RecordingModels()
    assert run(paths, models) == 1

    text = paths.notes.read_text()
    archive = paths.audio_archive / "2026-09"
    assert "## 16th September 2026, 5:56pm - A title" in text
    assert f"[audio]({archive / stem}.wav)" in text and "Cleaned: hello there" in text
    assert (archive / f"{stem}.wav").exists() and (archive / f"{stem}.json").exists()
    assert (paths.transcripts / f"{stem}.txt").read_text() == "hello there"
    assert not list(paths.inbox.iterdir())


def test_rerun_after_crash_does_not_duplicate(paths):
    stem, models = add_note(paths), RecordingModels()
    processor.process_note(paths.inbox / f"{stem}.json", paths, models, models)
    add_note(paths, stem)  # simulate a crash before the files were archived
    assert run(paths, models) == 0
    assert paths.notes.read_text().count("A title") == 1
    assert len(models.translated) == 1


def test_cached_transcript_skips_whisper(paths):
    stem, models = add_note(paths), RecordingModels()
    (paths.transcripts / f"{stem}.txt").write_text("cached words")
    run(paths, models)
    assert models.transcribed == [] and models.translated == ["cached words"]


def test_silence_skips_llm(paths):
    add_note(paths)
    models = RecordingModels(transcript="  ")
    run(paths, models)
    assert models.translated == [] and "(no speech detected)" in paths.notes.read_text()


def test_approximate_time_is_flagged(paths):
    add_note(paths, time_source="received")
    run(paths, RecordingModels())
    assert "5:56pm (approx.)" in paths.notes.read_text()


def test_failures_retry_then_move_to_failed(paths):
    stem, models = add_note(paths), RecordingModels(error=ValueError("bad JSON from LLM"))
    for attempt in (1, 2):
        run(paths, models, max_attempts=3)
        assert json.loads((paths.state / "attempts.json").read_text()) == {stem: attempt}
    run(paths, models, max_attempts=3)
    assert {p.name for p in paths.failed.iterdir()} == {f"{stem}.wav", f"{stem}.json", f"{stem}.error.txt"}
    assert "bad JSON from LLM" in (paths.failed / f"{stem}.error.txt").read_text()
    assert json.loads((paths.state / "attempts.json").read_text()) == {}


def test_model_unavailable_stops_without_counting(paths):
    add_note(paths, "a"), add_note(paths, "b")
    models = RecordingModels(error=ConnectionError("Ollama not running"))
    run(paths, models)
    assert len(models.translated) == 1, "stops after the first note"
    assert json.loads((paths.state / "attempts.json").read_text()) == {}
    assert len(list(paths.inbox.glob("*.json"))) == 2


def test_single_instance_lock(paths):
    lock = paths.state / "processor.lock"
    with processor.single_instance(lock) as first, processor.single_instance(lock) as second:
        assert first and not second


def test_build_models_reads_config_and_prompts():
    cfg = {"whisper": {"model": "w"},
           "ollama": {"model": "g", "system_prompt": "mac/prompts/system_prompt.md",
                      "examples": "mac/prompts/examples.json", "options": {"temperature": 0.4}}}
    transcriber, translator = processor.build_models(cfg)
    assert (transcriber.speech_to_text_model, transcriber.language) == ("w", "en")
    assert isinstance(translator, Translator) and translator.options == {"temperature": 0.4}

    messages = translator.get_messages("my transcript")
    examples = json.loads((REPO_ROOT / "mac/prompts/examples.json").read_text())
    assert messages[0]["role"] == "system" and "voice notes" in messages[0]["content"]
    assert len(messages) == 2 + 2 * len(examples)
    assert messages[-1] == {"role": "user", "content": "my transcript"}
