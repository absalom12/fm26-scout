import pathlib

import fmsave
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def scout(tmp_path, monkeypatch):
    monkeypatch.setenv("FM26_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FM26_SAVES_DIR", str(tmp_path / "saves"))
    (tmp_path / "saves").mkdir()
    import app
    monkeypatch.setattr(app, "STATUS", "empty")
    return app


@pytest.fixture
def client(scout):
    return TestClient(scout.app, base_url="http://127.0.0.1:8000")  # not a context manager: startup loader does not run


def test_data_routes_409_when_empty(client):
    for r in (client.get("/api/meta"), client.post("/api/players", json={}), client.get("/api/club")):
        assert r.status_code == 409 and r.json()["detail"] == "no_save"


def test_data_routes_409_loading(scout, client):
    scout.STATUS = "loading"
    assert client.get("/api/meta").json()["detail"] == "loading"


def test_status_identifies_app(client):
    assert client.get("/api/status").json() == {"app": "fm26-scout", "status": "empty"}


def test_saves_lists_and_reports_missing_folder(scout, client, tmp_path, monkeypatch):
    (tmp_path / "saves" / "Chantilly (v02).fm").write_bytes(b"x")
    d = client.get("/api/saves").json()
    assert d["folder_found"] and [s["name"] for s in d["saves"]] == ["Chantilly (v02).fm"]
    monkeypatch.setenv("FM26_SAVES_DIR", str(tmp_path / "nope"))
    assert client.get("/api/saves").json() == {"folder_found": False, "saves": []}


@pytest.mark.parametrize("name", ["../x.fm", "x.txt", "missing.fm", ""])
def test_load_save_rejects_bad_names(client, name):
    assert client.post("/api/load-save", json={"name": name}).status_code == 400


def test_load_save_success_replaces_data(scout, client, tmp_path, monkeypatch):
    (tmp_path / "saves" / "a.fm").write_bytes(b"x")
    data = tmp_path / "data"; data.mkdir()
    (data / "club.json").write_text("stale")

    def fake_extract(src, out):
        (pathlib.Path(out) / "all_world_players.csv").write_text("new")

    def fake_load(d=None):
        scout.con().execute("CREATE OR REPLACE TABLE players AS SELECT 1 AS uid")
        scout.GAME_DATE = "2030-01-01"

    monkeypatch.setattr(scout, "extract", fake_extract)
    monkeypatch.setattr(scout, "load", fake_load)
    r = client.post("/api/load-save", json={"name": "a.fm"})
    assert r.status_code == 200 and r.json() == {"rows": 1, "game_date": "2030-01-01"}
    assert (data / "all_world_players.csv").read_text() == "new"
    assert not (data / "club.json").exists() and scout.STATUS == "ready"


def test_load_save_invalid_save_keeps_previous_data(scout, client, tmp_path, monkeypatch):
    (tmp_path / "saves" / "bad.fm").write_bytes(b"not a save")
    data = tmp_path / "data"; data.mkdir()
    (data / "all_world_players.csv").write_text("old")
    scout.STATUS = "ready"

    def boom(src, out):
        raise fmsave.FmsaveError("corrupt save")

    monkeypatch.setattr(scout, "extract", boom)
    r = client.post("/api/load-save", json={"name": "bad.fm"})
    assert r.status_code == 400 and r.json()["detail"] == "corrupt save"
    assert (data / "all_world_players.csv").read_text() == "old" and scout.STATUS == "ready"
    assert list(data.glob("tmp*")) == []  # temp extraction dir cleaned


def test_load_save_busy_returns_409(scout, client, tmp_path):
    (tmp_path / "saves" / "a.fm").write_bytes(b"x")
    assert scout._ingest_lock.acquire(blocking=False)
    try:
        r = client.post("/api/load-save", json={"name": "a.fm"})
        assert r.status_code == 409 and r.json()["detail"] == "busy"
    finally:
        scout._ingest_lock.release()


def test_idle_expired(scout, monkeypatch):
    monkeypatch.setattr(scout, "LAST_PING", 100.0)
    monkeypatch.setattr(scout, "BYE_AT", None)
    assert not scout.idle_expired(now=200.0) and scout.idle_expired(now=701.0)
    monkeypatch.setattr(scout, "BYE_AT", 150.0)
    assert not scout.idle_expired(now=149.0) and scout.idle_expired(now=151.0)


def test_upload_fallback_rejects_non_fm_and_loads_fm(scout, client, monkeypatch):
    assert client.post("/api/upload", files={"file": ("x.txt", b"x")}).status_code == 400

    def fake_load(d=None):
        scout.con().execute("CREATE OR REPLACE TABLE players AS SELECT 1 AS uid")
        scout.GAME_DATE = "2031-01-01"

    monkeypatch.setattr(scout, "extract", lambda src, out: (pathlib.Path(out) / "all_world_players.csv").write_text("up"))
    monkeypatch.setattr(scout, "load", fake_load)
    r = client.post("/api/upload", files={"file": ("My Save.FM", b"bytes")})
    assert r.status_code == 200 and r.json() == {"rows": 1, "game_date": "2031-01-01"}


def test_rejects_foreign_host_and_origin(client):
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 403
    assert client.post("/api/bye", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/bye", headers={"origin": "http://localhost:8000"}).status_code == 200


def test_rejects_oversized_upload(scout, client, monkeypatch):
    monkeypatch.setattr(scout, "MAX_UPLOAD", 10)
    r = client.post("/api/upload", files={"file": ("a.fm", b"x" * 100)})
    assert r.status_code == 413


def test_club_leagues_picks_biggest_domestic_table(scout):
    tables = [(1, 20, [10, 11, None]), (2, 36, [10]), (3, 8, [10, 12]), (4, 5, [13])]
    # club 10 is in a 20-club league, a 36-club league phase (too big) and an 8-club table (smaller); 13's table is too small
    assert scout.club_leagues(tables) == {10: 1, 11: 1, 12: 3}
