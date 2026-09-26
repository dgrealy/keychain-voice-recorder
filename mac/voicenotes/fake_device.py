"""Pretend to be the keychain: upload a WAV exactly as the firmware does.

Useful for testing the receiver and processor without hardware::

    python -m voicenotes.fake_device                    # 2 s test tone to localhost
    python -m voicenotes.fake_device --wav memo.wav     # your own 16 kHz mono WAV
    python -m voicenotes.fake_device --unsynced         # device clock never set
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
import struct
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from .config import load_config

SAMPLE_RATE = 16000


def make_wav(seconds: float = 2.0, freq: float = 440.0) -> bytes:
    """Return a 16 kHz, 16-bit mono WAV containing a sine tone."""
    n = int(seconds * SAMPLE_RATE)
    frames = struct.pack(f"<{n}h", *(int(8000 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)) for i in range(n)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(frames)
    return buf.getvalue()


def make_meta(duration_s: float, seq: int | None = None, synced: bool = True, **overrides) -> dict:
    """Return a sidecar dict shaped like the firmware's (``docs/PROTOCOL.md`` section 1)."""
    now = int(time.time())
    meta = {"schema": 1, "device_id": "fake-device", "fw_version": "fake", "seq": seq or random.randint(1, 10**9),
            "boot_id": "0badc0de", "time_synced": synced, "started_at": now - 60 if synced else 1000,
            "started_mono": 1000, "duration_s": duration_s, "sample_rate": SAMPLE_RATE, "bits_per_sample": 16,
            "channels": 1, "stop_reason": "button_off", "battery_v": 3.9, "dropped_ms": 0}
    return {**meta, **overrides}


def upload(url: str, token: str, wav: bytes, meta: dict, device_mono: int = 1060,
           boot_id: str | None = None, sha256: str | None = None) -> tuple[int, dict]:
    """POST one note to ``<url>/upload``.

    Parameters
    ----------
    url : str
        Receiver base URL, e.g. ``"http://localhost:8765"``.
    token : str
        Shared secret.
    wav : bytes
        Request body.
    meta : dict
        Sidecar sent in ``X-VN-Meta``.
    device_mono : int, optional
        Current monotonic clock (default: 60 s after the default ``started_mono``).
    boot_id : str, optional
        Current boot id (default: the one in ``meta``, i.e. no power loss since recording).
    sha256 : str, optional
        Override the checksum (to test corruption).

    Returns
    -------
    status : int
        HTTP status code.
    body : dict
        Parsed JSON response.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "audio/wav", "X-VN-Protocol": "1",
               "X-VN-Filename": f"rec_{meta.get('seq', 0):06d}.wav",
               "X-VN-SHA256": sha256 or hashlib.sha256(wav).hexdigest(),
               "X-VN-Meta": json.dumps(meta, separators=(",", ":")),
               "X-VN-Boot-Id": boot_id or meta.get("boot_id", ""),
               "X-VN-Device-Mono": str(device_mono), "X-VN-Device-Time": str(int(time.time()))}
    req = urllib.request.Request(f"{url}/upload", data=wav, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description="Upload a test note like the keychain would.")
    ap.add_argument("--config")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--wav", type=Path, help="16 kHz mono WAV to send (default: 2 s tone)")
    ap.add_argument("--unsynced", action="store_true", help="pretend the device clock was never set")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)["receiver"]
    wav = args.wav.read_bytes() if args.wav else make_wav()
    duration = (len(wav) - 44) / (2 * SAMPLE_RATE)
    status, body = upload(f"http://{args.host}:{cfg.get('port', 8765)}", cfg["token"], wav,
                          make_meta(round(duration, 2), synced=not args.unsynced))
    print(status, body)


if __name__ == "__main__":
    main()
