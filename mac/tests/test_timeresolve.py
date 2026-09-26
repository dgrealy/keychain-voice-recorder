from datetime import datetime, timedelta, timezone

from voicenotes.timeresolve import resolve_recorded_at

NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
META = {"time_synced": False, "started_at": 0, "boot_id": "aaaa", "started_mono": 1000}


def test_device_clock_used_when_synced():
    t = NOW - timedelta(hours=2)
    assert resolve_recorded_at({**META, "time_synced": True, "started_at": t.timestamp()}, NOW, "aaaa", 5000) \
        == (t, "device_clock")


def test_relative_when_unsynced_but_same_boot():
    assert resolve_recorded_at(META, NOW, "aaaa", 1600) == (NOW - timedelta(seconds=600), "relative")


def test_received_when_power_was_lost():
    assert resolve_recorded_at(META, NOW, "bbbb", 1600) == (NOW, "received")


def test_future_device_clock_falls_back_to_relative():
    future = (NOW + timedelta(hours=1)).timestamp()
    assert resolve_recorded_at({**META, "time_synced": True, "started_at": future}, NOW, "aaaa", 1060)[1] == "relative"


def test_negative_age_and_missing_mono_fall_back_to_received():
    assert resolve_recorded_at(META, NOW, "aaaa", 10)[1] == "received"
    assert resolve_recorded_at(META, NOW, "aaaa", None)[1] == "received"
