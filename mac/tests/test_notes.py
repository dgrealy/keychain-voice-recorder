from datetime import datetime
from pathlib import Path

import pytest

from voicenotes import notes


@pytest.mark.parametrize("n, expected", [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"),
                                         (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"), (31, "31st")])
def test_ordinal(n, expected):
    assert notes.ordinal(n) == expected


@pytest.mark.parametrize("dt, expected", [
    (datetime(2026, 9, 16, 17, 56), "16th September 2026, 5:56pm"),
    (datetime(2026, 1, 1, 0, 5), "1st January 2026, 12:05am"),
    (datetime(2026, 3, 22, 12, 0), "22nd March 2026, 12:00pm"),
    (datetime(2026, 3, 23, 9, 7), "23rd March 2026, 9:07am"),
])
def test_format_custom_datetime(dt, expected):
    assert notes.format_custom_datetime(dt) == expected


def test_entry_matches_example_layout():
    entry = notes.format_entry("s1", datetime(2026, 9, 16, 17, 56), "robust routines", "Body.\n",
                               Path("/a/test1.wav"), Path("/t/test1.txt"))
    assert entry == ("<!-- voicenote:s1 -->\n"
                     "## 16th September 2026, 5:56pm - robust routines\n\n"
                     "[audio](/a/test1.wav) $\\cdot$ [transcript](/t/test1.txt)\n\n"
                     "Body.\n\n")


def test_entry_approximate_time_and_paths_with_spaces():
    entry = notes.format_entry("s1", datetime(2026, 9, 16, 17, 56), "t", "c",
                               Path("/My Notes/a.wav"), Path("/t.txt"), approximate=True)
    assert "5:56pm (approx.) - t" in entry
    assert "[audio](</My Notes/a.wav>)" in entry


def test_append_is_idempotent(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("existing text without newline")
    assert notes.append_entry(path, "s1", "<!-- voicenote:s1 -->\nentry\n")
    assert not notes.append_entry(path, "s1", "<!-- voicenote:s1 -->\nentry\n")
    assert path.read_text() == "existing text without newline\n<!-- voicenote:s1 -->\nentry\n"
    assert notes.has_entry(path, "s1") and not notes.has_entry(path, "s2")
