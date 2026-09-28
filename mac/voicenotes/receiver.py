"""Receiver: accepts voice notes from the keychain and drops them in the inbox.

Implements the Mac side of ``docs/PROTOCOL.md`` sections 2 to 4. Run with::

    python -m voicenotes.receiver
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO, Mapping

from .config import Paths, load_config, setup_logging
from .timeresolve import resolve_recorded_at

PROTOCOL = 1
MAX_BYTES = 512 * 1024 * 1024
CHUNK = 64 * 1024
REQUIRED_META = ("device_id", "seq", "boot_id", "time_synced", "started_at", "started_mono", "duration_s")

log = logging.getLogger("receiver")


class UploadError(Exception):
    """An upload rejected with an HTTP status code.

    Parameters
    ----------
    status : int
        HTTP status to send back.
    message : str
        Reason, returned to the device and logged.
    """

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Index:
    """SQLite record of every stored note, used for de-duplication and unique names.

    Parameters
    ----------
    db_path : Path
        Database file, created if missing.
    """

    def __init__(self, db_path: Path):
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS notes (device_id TEXT, seq INTEGER, sha256 TEXT, "
                         "name TEXT UNIQUE, received_at TEXT, PRIMARY KEY (device_id, seq, sha256))")

    def find(self, device_id: str, seq: int, sha256: str) -> str | None:
        """Return the inbox name of an already-stored note, or ``None``."""
        row = self._db.execute("SELECT name FROM notes WHERE device_id=? AND seq=? AND sha256=?",
                               (device_id, seq, sha256)).fetchone()
        return row[0] if row else None

    def has_name(self, name: str) -> bool:
        """Whether ``name`` has ever been used, even if it has since been archived."""
        return self._db.execute("SELECT 1 FROM notes WHERE name=?", (name,)).fetchone() is not None

    def add(self, device_id: str, seq: int, sha256: str, name: str, received_at: str) -> None:
        """Record a stored note."""
        with self._db:
            self._db.execute("INSERT INTO notes VALUES (?, ?, ?, ?, ?)", (device_id, seq, sha256, name, received_at))


def parse_meta(raw: str | None) -> dict:
    """Parse and validate the ``X-VN-Meta`` header.

    Raises
    ------
    UploadError
        400 if the header is missing, not a JSON object, or missing a required field.
    """
    try:
        meta = json.loads(raw or "")
    except json.JSONDecodeError as e:
        raise UploadError(400, f"X-VN-Meta is not valid JSON: {e}") from None
    if not isinstance(meta, dict):
        raise UploadError(400, "X-VN-Meta must be a JSON object")
    missing = [k for k in REQUIRED_META if k not in meta]
    if missing:
        raise UploadError(400, f"X-VN-Meta missing {', '.join(missing)}")
    return meta


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def receive_body(stream: BinaryIO, length: int, dest: Path) -> tuple[str, bytes]:
    """Copy exactly ``length`` bytes from ``stream`` to ``dest`` while hashing them.

    Returns
    -------
    sha256 : str
        Lowercase hex digest.
    head : bytes
        The first 12 bytes, used to check the RIFF/WAVE header.

    Raises
    ------
    UploadError
        400 if the connection ends early.
    """
    sha, head, remaining = hashlib.sha256(), b"", length
    with dest.open("wb") as f:
        while remaining:
            chunk = stream.read(min(CHUNK, remaining))
            if not chunk:
                raise UploadError(400, f"body ended {remaining} bytes early")
            if len(head) < 12:
                head += chunk[:12 - len(head)]
            sha.update(chunk)
            f.write(chunk)
            remaining -= len(chunk)
    return sha.hexdigest(), head


def write_json_atomic(path: Path, data: dict, tmp_dir: Path) -> None:
    """Write JSON to ``tmp_dir`` and rename it into place, so readers never see a partial file."""
    tmp = tmp_dir / f"{uuid.uuid4().hex}.json.tmp"
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


class Receiver:
    """Validates uploads and moves them into the inbox.

    Parameters
    ----------
    paths : Paths
        File layout. Folders are created if missing.
    token : str
        Shared secret the device sends as ``Authorization: Bearer <token>``.
    low_battery_volts : float, optional
        A warning is logged when a note reports a battery voltage below this.
    """

    def __init__(self, paths: Paths, token: str, low_battery_volts: float | None = None):
        if not token:
            raise ValueError("receiver.token is empty in config.yaml")
        self.paths = paths.ensure()
        self.low_battery_volts = low_battery_volts
        self._auth = f"Bearer {token}".encode()
        self._index = Index(paths.state / "received.sqlite")
        self._lock = threading.Lock()

    def store(self, headers: Mapping[str, str], body: BinaryIO, length: int | None) -> dict:
        """Validate one upload and move it into the inbox.

        Parameters
        ----------
        headers : Mapping[str, str]
            Request headers (case-insensitive mapping, as provided by ``http.server``).
        body : BinaryIO
            Request body stream.
        length : int or None
            ``Content-Length``.

        Returns
        -------
        dict
            ``{"status": "stored" | "duplicate", "name": <inbox stem>}``.

        Raises
        ------
        UploadError
            For any rejection listed in ``docs/PROTOCOL.md``.
        """
        if not hmac.compare_digest(headers.get("Authorization", "").encode(), self._auth):
            raise UploadError(401, "bad or missing token")
        if length is None:
            raise UploadError(411, "Content-Length required")
        if length > MAX_BYTES:
            raise UploadError(413, f"{length} bytes is over the {MAX_BYTES} limit")
        meta = parse_meta(headers.get("X-VN-Meta"))

        part = self.paths.incoming / f"{uuid.uuid4().hex}.part"
        try:
            sha, head = receive_body(body, length, part)
            if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                raise UploadError(415, "body is not a WAV file")
            if sha != headers.get("X-VN-SHA256", "").lower():
                raise UploadError(422, "SHA-256 mismatch")
            with self._lock:
                name = self._index.find(meta["device_id"], meta["seq"], sha)
                if name:
                    log.info("Duplicate of %s ignored", name)
                    return {"status": "duplicate", "name": name}
                name = self._save(part, meta, sha, headers)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise UploadError(507, "Mac disk is full") from e
            raise
        finally:
            part.unlink(missing_ok=True)

        battery = meta.get("battery_v")
        log.info("Received %s (%.1fs, battery %s)", name, meta["duration_s"], f"{battery:.2f}V" if battery else "n/a")
        if battery and self.low_battery_volts and battery < self.low_battery_volts:
            log.warning("Keychain battery low (%.2fV): charge it soon", battery)
        return {"status": "stored", "name": name}

    def _save(self, part: Path, meta: dict, sha: str, headers: Mapping[str, str]) -> str:
        """Resolve the time, then move the WAV and (last) the JSON into the inbox. Returns the stem."""
        received = datetime.now(timezone.utc).astimezone()
        recorded, source = resolve_recorded_at(meta, received, headers.get("X-VN-Boot-Id"),
                                               _int_or_none(headers.get("X-VN-Device-Mono")))
        recorded = recorded.astimezone()
        name = self._unique_name(f"{recorded:%Y-%m-%d_%H-%M-%S}")
        sidecar = {**meta,
                   "recorded_at": recorded.isoformat(timespec="seconds"),
                   "time_source": source,
                   "received_at": received.isoformat(timespec="seconds"),
                   "sha256": sha,
                   "original_filename": headers.get("X-VN-Filename", "")}
        os.replace(part, self.paths.inbox / f"{name}.wav")
        write_json_atomic(self.paths.inbox / f"{name}.json", sidecar, self.paths.incoming)
        self._index.add(meta["device_id"], meta["seq"], sha, name, sidecar["received_at"])
        return name

    def _unique_name(self, stem: str) -> str:
        return unique_name(self._index, self.paths.inbox, stem)


def unique_name(index: Index, inbox: Path, stem: str) -> str:
    """Return ``stem``, or ``stem_2``, ``stem_3`` ... if it is already taken."""
    name, n = stem, 1
    while index.has_name(name) or (inbox / f"{name}.wav").exists():
        n += 1
        name = f"{stem}_{n}"
    return name


class Handler(BaseHTTPRequestHandler):
    """Routes ``GET /health`` and ``POST /upload`` to the server's :class:`Receiver`."""

    server_version = "VoiceNoteReceiver/1"

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if self.path == "/health":
            self._reply(200, {"status": "ok", "protocol": PROTOCOL})
        else:
            self._reply(404, {"status": "error", "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/upload":
            return self._reply(404, {"status": "error", "error": "not found"})
        try:
            length = _int_or_none(self.headers.get("Content-Length"))
            self._reply(200, self.server.receiver.store(self.headers, self.rfile, length))
        except UploadError as e:
            log.warning("Rejected upload from %s: %s", self.client_address[0], e)
            self._reply(e.status, {"status": "error", "error": str(e)})

    def _reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        log.debug(fmt, *args)


def make_server(receiver: Receiver, host: str = "0.0.0.0", port: int = 8765) -> ThreadingHTTPServer:
    """Create (but do not start) the HTTP server. Port 0 picks a free port."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.receiver = receiver
    return server


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description="Receive voice notes from the keychain.")
    ap.add_argument("--config", help="config file (default: <repo>/config.yaml)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, help="overrides receiver.port")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    paths = Paths.from_config(cfg).ensure()
    setup_logging(paths.log)
    receiver = Receiver(paths, cfg["receiver"].get("token", ""), cfg.get("device", {}).get("low_battery_volts"))
    server = make_server(receiver, args.host, args.port or cfg["receiver"].get("port", 8765))
    log.info("Listening on port %d; inbox %s", server.server_address[1], paths.inbox)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
