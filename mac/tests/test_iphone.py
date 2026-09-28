import json
import os
import shutil
import subprocess
import time
import wave
from datetime import datetime, timedelta, timezone

import pytest

from voicenotes import processor
from voicenotes.fake_device import make_wav
from voicenotes.iphone import Importer, ffmpeg_to_wav, ffprobe_creation_time, time_from_filename

from test_processor import RecordingModels

LONG_AGO = datetime(2020, 1, 1, tzinfo=timezone.utc)


def fake_convert(src, dst):
    dst.write_bytes(make_wav(0.5))


def importer(paths, folder, probe=lambda p: None, **kw):
    kw.setdefault("since", LONG_AGO)
    return Importer(paths, [folder], probe=probe, convert=fake_convert, settle_seconds=0, **kw)


def add_memo(folder, name="20260926 154210-1A2B3C4D.m4a", data=b"memo audio", age_s=120):
    p = folder / name
    p.write_bytes(data)
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


@pytest.fixture
def memos(tmp_path):
    d = tmp_path / "Recordings"
    d.mkdir()
    return d


def test_time_from_filename():
    assert time_from_filename("20260926 154210-1A2B3C4D.m4a").replace(tzinfo=None) == datetime(2026, 9, 26, 15, 42, 10)
    assert time_from_filename("2026-09-26_15-42-10.m4a").replace(tzinfo=None) == datetime(2026, 9, 26, 15, 42, 10)
    assert time_from_filename("New Recording 3.m4a") is None


def test_imports_into_inbox_like_the_receiver(paths, memos):
    add_memo(memos)
    assert importer(paths, memos).run_once() == 1

    [meta_path] = paths.inbox.glob("*.json")
    assert meta_path.stem == "2026-09-26_15-42-10"
    assert meta_path.with_suffix(".wav").read_bytes()[:4] == b"RIFF"
    meta = json.loads(meta_path.read_text())
    assert meta["time_source"] == "device_clock" and meta["device_id"] == "iphone"
    assert meta["original_filename"] == "20260926 154210-1A2B3C4D.m4a" and meta["duration_s"] == 0.5
    assert (memos / "20260926 154210-1A2B3C4D.m4a").exists(), "source is left alone"
    assert not list(paths.incoming.iterdir())


def test_embedded_creation_time_wins(paths, memos):
    add_memo(memos)
    probe = lambda p: datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    importer(paths, memos, probe).run_once()
    meta = json.loads(next(paths.inbox.glob("*.json")).read_text())
    assert datetime.fromisoformat(meta["recorded_at"]) == datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def test_unknown_time_is_approximate(paths, memos):
    add_memo(memos, "New Recording.m4a")
    importer(paths, memos).run_once()
    assert json.loads(next(paths.inbox.glob("*.json")).read_text())["time_source"] == "received"


def test_each_recording_is_imported_once(paths, memos):
    add_memo(memos)
    assert importer(paths, memos).run_once() == 1
    processor.run_once(paths, RecordingModels(), RecordingModels())  # archives it
    assert importer(paths, memos).run_once() == 0

    # Same audio under a new name/mtime (e.g. re-synced): still a duplicate.
    add_memo(memos, "20260926 154210-1A2B3C4D copy.m4a", age_s=60)
    assert importer(paths, memos).run_once() == 0
    assert not list(paths.inbox.iterdir())


def test_first_run_ignores_existing_library(paths, memos):
    add_memo(memos)
    assert Importer(paths, [memos], probe=lambda p: None, convert=fake_convert, settle_seconds=0).run_once() == 0
    # A new recording made after the first run is picked up.
    add_memo(memos, datetime.now().strftime("%Y-%m-%d_%H-%M-%S.m4a"), data=b"new", age_s=0)
    assert Importer(paths, [memos], probe=lambda p: None, convert=fake_convert, settle_seconds=0).run_once() == 1


def test_waits_until_file_settles(paths, memos):
    add_memo(memos, age_s=0)
    assert Importer(paths, [memos], since=LONG_AGO, probe=lambda p: None, convert=fake_convert,
                    settle_seconds=30).run_once() == 0
    assert importer(paths, memos).run_once() == 1, "not marked as seen while settling"


def test_retries_then_gives_up(paths, memos):
    add_memo(memos)

    def broken(src, dst):
        raise RuntimeError("ffmpeg failed")

    imp = lambda: Importer(paths, [memos], since=LONG_AGO, probe=lambda p: None, convert=broken, settle_seconds=0)
    for _ in range(3):
        assert imp().run_once() == 0
    assert importer(paths, memos).run_once() == 0, "gave up after 3 attempts"
    assert not list(paths.inbox.iterdir()) and not list(paths.incoming.iterdir())


def test_missing_folder_is_skipped(paths, tmp_path):
    assert importer(paths, tmp_path / "nope").run_once() == 0


def test_imported_note_goes_through_processor(paths, memos):
    add_memo(memos)
    importer(paths, memos).run_once()
    assert processor.run_once(paths, RecordingModels(), RecordingModels()) == 1
    assert "voicenote:2026-09-26_15-42-10" in paths.notes.read_text()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_real_ffmpeg_conversion(tmp_path):
    src = tmp_path / "memo.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-ar", "44100", "-ac", "2", "-metadata", "creation_time=2026-09-26T14:42:10Z", str(src)], check=True)
    dst = tmp_path / "out.wav"
    ffmpeg_to_wav(src, dst)
    with wave.open(str(dst)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
    assert ffprobe_creation_time(src) == datetime(2026, 9, 26, 14, 42, 10, tzinfo=timezone.utc)
