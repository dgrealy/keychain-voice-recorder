import json
import urllib.request

import pytest

from voicenotes.fake_device import make_meta, make_wav, upload

from conftest import TOKEN


@pytest.fixture
def wav():
    return make_wav(0.5)


def test_health(server_url):
    with urllib.request.urlopen(f"{server_url}/health") as r:
        assert json.loads(r.read()) == {"status": "ok", "protocol": 1}


def test_stores_wav_and_sidecar(server_url, paths, wav):
    meta = make_meta(0.5, seq=7)
    status, body = upload(server_url, TOKEN, wav, meta)
    assert (status, body["status"]) == (200, "stored")

    name = body["name"]
    assert (paths.inbox / f"{name}.wav").read_bytes() == wav
    sidecar = json.loads((paths.inbox / f"{name}.json").read_text())
    assert sidecar["seq"] == 7 and sidecar["time_source"] == "device_clock"
    assert sidecar["original_filename"] == "rec_000007.wav"
    assert {"recorded_at", "received_at", "sha256"} <= sidecar.keys()
    assert not list(paths.incoming.iterdir()), "temporary files are cleaned up"


def test_unsynced_note_gets_relative_time(server_url, paths, wav):
    _, body = upload(server_url, TOKEN, wav, make_meta(0.5, synced=False), device_mono=1300)
    assert json.loads((paths.inbox / f"{body['name']}.json").read_text())["time_source"] == "relative"


def test_duplicate_upload_is_acknowledged_once(server_url, paths, wav):
    meta = make_meta(0.5, seq=9)
    first, second = upload(server_url, TOKEN, wav, meta), upload(server_url, TOKEN, wav, meta)
    assert second == (200, {"status": "duplicate", "name": first[1]["name"]})
    assert len(list(paths.inbox.glob("*.wav"))) == 1


def test_same_second_gets_unique_names(server_url, wav):
    meta = make_meta(0.5, seq=1)
    a = upload(server_url, TOKEN, wav, meta)[1]["name"]
    b = upload(server_url, TOKEN, wav, {**meta, "seq": 2})[1]["name"]
    assert b == f"{a}_2"


def test_rejects_checksum_mismatch(server_url, paths, wav):
    assert upload(server_url, TOKEN, wav, make_meta(0.5), sha256="0" * 64)[0] == 422
    assert not list(paths.inbox.iterdir()) and not list(paths.incoming.iterdir())


def test_rejects_bad_token(server_url, paths, wav):
    assert upload(server_url, "wrong", wav, make_meta(0.5))[0] == 401
    assert not list(paths.inbox.iterdir())


def test_rejects_non_wav(server_url):
    assert upload(server_url, TOKEN, b"not a wav file at all", make_meta(0.5))[0] == 415


def test_rejects_incomplete_meta(server_url, wav):
    meta = make_meta(0.5)
    del meta["boot_id"]
    status, body = upload(server_url, TOKEN, wav, meta)
    assert status == 400 and "boot_id" in body["error"]


def test_low_battery_is_logged(server_url, wav, caplog):
    upload(server_url, TOKEN, wav, make_meta(0.5, battery_v=3.3))
    assert "battery low" in caplog.text
