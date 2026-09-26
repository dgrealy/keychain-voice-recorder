"""Configuration, file layout and logging shared by the receiver and the processor.

Everything comes from ``config.yaml`` at the repository root (a filled-in copy of
``config.example.yaml``). Set ``VOICENOTES_CONFIG`` to point at a different file.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_config(path: str | os.PathLike | None = None) -> dict:
    """Read the YAML configuration.

    Parameters
    ----------
    path : str or path-like, optional
        Config file. Defaults to ``$VOICENOTES_CONFIG``, then ``<repo>/config.yaml``.

    Returns
    -------
    dict
        The parsed configuration.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    """
    path = Path(path or os.environ.get("VOICENOTES_CONFIG") or REPO_ROOT / "config.yaml")
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: copy config.example.yaml to config.yaml and fill it in")
    return yaml.safe_load(path.read_text()) or {}


def resolve(path: str | os.PathLike, base: Path) -> Path:
    """Expand ``~`` in ``path`` and, if it is relative, anchor it at ``base``.

    Parameters
    ----------
    path : str or path-like
        Path from the config file.
    base : Path
        Directory that relative paths are relative to.

    Returns
    -------
    Path
        Absolute path.
    """
    p = Path(path).expanduser()
    return p if p.is_absolute() else base / p


@dataclass(frozen=True)
class Paths:
    """Every file and folder the Mac side uses (see ``docs/PROTOCOL.md`` section 5).

    Parameters
    ----------
    base : Path
        ``mac.base_dir``. Every folder below lives inside it.
    notes : Path
        The ``notes.md`` file.
    log : Path
        The shared log file.
    """

    base: Path
    notes: Path
    log: Path

    @classmethod
    def from_config(cls, cfg: dict) -> "Paths":
        """Build from the ``mac`` section of the configuration."""
        mac = cfg.get("mac", {})
        base = resolve(mac.get("base_dir", "~/VoiceNotes"), Path.home())
        return cls(base, resolve(mac.get("notes_file", "notes.md"), base),
                   resolve(mac.get("log_file", "voicenotes.log"), base))

    inbox = property(lambda self: self.base / "inbox")
    audio_archive = property(lambda self: self.base / "archive" / "audio")
    transcripts = property(lambda self: self.base / "archive" / "transcripts")
    failed = property(lambda self: self.base / "failed")
    state = property(lambda self: self.base / ".state")
    incoming = property(lambda self: self.base / ".state" / "incoming")

    def ensure(self) -> "Paths":
        """Create every folder that does not exist yet, and return ``self``."""
        for d in (self.inbox, self.audio_archive, self.transcripts, self.failed, self.incoming,
                  self.notes.parent, self.log.parent):
            d.mkdir(parents=True, exist_ok=True)
        return self


def setup_logging(log_file: Path, level: int = logging.INFO) -> None:
    """Send log records to ``log_file`` and to stderr.

    Parameters
    ----------
    log_file : Path
        Appended to. The receiver and processor share it.
    level : int, optional
        Minimum level to record.
    """
    fmt = logging.Formatter("%(asctime)s %(name)-10s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    for handler in (logging.FileHandler(log_file), logging.StreamHandler()):
        handler.setFormatter(fmt)
        root.addHandler(handler)
