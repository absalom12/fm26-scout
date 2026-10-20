import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tel(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUTSHEET_DATA_DIR", str(tmp_path / "data"))
    import telemetry
    sent = []
    monkeypatch.setattr(telemetry, "_post", sent.append)
    telemetry.sent = sent
    return telemetry


def queued(tel):
    return json.loads(tel._queue().read_text()) if tel._queue().exists() else {}


def test_nothing_is_recorded_before_or_after_saying_no(tel):
    assert tel.state() == {"asked": False, "on": False}
    tel.record("export", "full")
    tel.set_consent(False)
    tel.record("export", "full")
    assert tel.state() == {"asked": True, "on": False} and queued(tel) == {}


def test_counts_per_day_and_rejects_free_text(tel):
    tel.set_consent(True)
    tel.record("export", "full")
    tel.record("export", "full")
    tel.record("time", None, 30)
    tel.record("error", "C:\\Users\\me\\save.fm")  # a path never gets in
    (day,) = queued(tel).values()
    assert day == {"export:full": 2, "time": 30}


def test_flush_sends_then_clears(tel):
    tel.set_consent(True)
    tel.record("launch", "mac")
    tel.flush("1.2.3")
    (body,) = tel.sent
    assert body["v"] == "1.2.3" and body["id"] == json.loads(tel._cfg().read_text())["id"]
    assert list(body["days"].values()) == [{"launch:mac": 1}] and queued(tel) == {}


def test_offline_keeps_the_queue(tel, monkeypatch):
    tel.set_consent(True)
    tel.record("search", "full")

    def offline(body):
        tel.record("search", "full")  # recorded while the upload runs
        raise OSError("no network")
    monkeypatch.setattr(tel, "_post", offline)
    tel.flush("1.2.3")
    (day,) = queued(tel).values()
    assert day == {"search:full": 2}


def test_opting_out_deletes_local_and_asks_the_server_to_forget(tel, monkeypatch):
    tel.set_consent(True)
    first = json.loads(tel._cfg().read_text())["id"]
    tel.record("search", "full")
    tel.set_consent(False)
    assert queued(tel) == {}
    monkeypatch.setattr(tel, "_post", lambda b: (_ for _ in ()).throw(OSError()))
    tel.flush("1")  # offline: the forget request waits
    tel.set_consent(True)  # back on, with a new ID; the old one is still to forget
    assert json.loads(tel._cfg().read_text())["id"] != first
    monkeypatch.setattr(tel, "_post", tel.sent.append)
    tel.flush("1")
    assert tel.sent == [{"forget": [first]}] and json.loads(tel._cfg().read_text())["forget"] == []


def test_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUTSHEET_DATA_DIR", str(tmp_path / "data"))
    import app, telemetry
    monkeypatch.setattr(app, "STATUS", "empty")
    c = TestClient(app.app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False)
    assert c.get("/api/telemetry").json() == {"asked": False, "on": False}
    assert c.post("/api/telemetry", json={"on": True}).json() == {"asked": True, "on": True}
    c.post("/api/track", json={"e": "mode", "d": "moneyball"})
    c.post("/api/track", json={"e": "anything_else"})

    @app.app.get("/api/boom/{uid}")
    def boom(uid: int):
        raise KeyError("/Users/me/secret.fm")
    app.STATUS = "ready"
    assert c.get("/api/boom/7").status_code == 500
    (day,) = json.loads(telemetry._queue().read_text()).values()
    assert day == {"consent": 1, "mode:moneyball": 1, "error:api_boom_uid.KeyError": 1}
