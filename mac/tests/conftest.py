"""Shared fixtures: a throwaway VoiceNotes folder and a running receiver."""
from __future__ import annotations

import threading

import pytest

from voicenotes.config import Paths
from voicenotes.receiver import Receiver, make_server

TOKEN = "test-token"


@pytest.fixture
def paths(tmp_path):
    """A fresh VoiceNotes folder layout under ``tmp_path``."""
    return Paths(tmp_path, tmp_path / "notes.md", tmp_path / "voicenotes.log").ensure()


@pytest.fixture
def server_url(paths):
    """Start a receiver on a free port and yield its base URL."""
    server = make_server(Receiver(paths, TOKEN, low_battery_volts=3.5), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
