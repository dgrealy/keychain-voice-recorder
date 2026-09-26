"""Formatting and appending ``notes.md`` entries.

The layout follows ``docs/example_notes.md``::

    ## 16th September 2026, 5:56pm - <title>

    [audio](<wav path>) $\\cdot$ [transcript](<txt path>)

    <content>

Each entry is preceded by an invisible ``<!-- voicenote:<stem> -->`` marker, so a note is
never added twice, even if processing is interrupted and re-run.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

MARKER = "<!-- voicenote:{} -->"


def ordinal(n: int) -> str:
    """Return ``n`` with its English suffix: 1st, 2nd, 3rd, 4th, 11th, 12th, 13th, 21st ..."""
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def format_custom_datetime(dt: datetime) -> str:
    """Format a time the way the note headings show it.

    Parameters
    ----------
    dt : datetime
        Local time.

    Returns
    -------
    str
        For example ``"16th September 2026, 5:56pm"``.
    """
    hour = dt.hour % 12 or 12
    return f"{ordinal(dt.day)} {dt:%B %Y}, {hour}:{dt:%M}{'am' if dt.hour < 12 else 'pm'}"


def _link(path: Path) -> str:
    """Markdown link target; angle brackets keep paths with spaces working."""
    return f"<{path}>" if " " in str(path) else str(path)


def format_entry(stem: str, recorded_at: datetime, title: str, content: str,
                 audio: Path, transcript: Path, approximate: bool = False) -> str:
    """Build one ``notes.md`` entry.

    Parameters
    ----------
    stem : str
        Inbox name of the note; used for the de-duplication marker.
    recorded_at : datetime
        When the note was recorded (local time).
    title, content : str
        The LLM's structured output.
    audio, transcript : Path
        Absolute paths of the archived WAV and the raw transcript.
    approximate : bool, optional
        Adds "(approx.)" to the heading when only the upload time was known.

    Returns
    -------
    str
        The entry, ending in a blank line.
    """
    when = format_custom_datetime(recorded_at) + (" (approx.)" if approximate else "")
    return (f"{MARKER.format(stem)}\n"
            f"## {when} - {title.strip()}\n\n"
            f"[audio]({_link(audio)}) $\\cdot$ [transcript]({_link(transcript)})\n\n"
            f"{content.strip()}\n\n")


def has_entry(notes_path: Path, stem: str) -> bool:
    """Whether ``notes_path`` already contains the entry for ``stem``."""
    return notes_path.exists() and MARKER.format(stem) in notes_path.read_text()


def append_entry(notes_path: Path, stem: str, entry: str) -> bool:
    """Append ``entry`` unless the note is already there.

    Returns
    -------
    bool
        ``True`` if the entry was written.
    """
    existing = notes_path.read_text() if notes_path.exists() else ""
    if MARKER.format(stem) in existing:
        return False
    with notes_path.open("a") as f:
        f.write(("\n" if existing and not existing.endswith("\n") else "") + entry)
    return True
