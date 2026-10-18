import json
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
    assert client.get("/api/status").json() == {"app": "fm26-scout", "status": "empty", "version": "dev", "progress": None,
                                                 "can_update": False, "update": {"state": None, "pct": 0, "error": None}}


def test_status_reports_progress_while_busy(scout, client):
    scout._start(85, 100)
    scout._step(.5, 1, "Scoring players")
    with scout._ingest_lock:
        assert client.get("/api/status").json()["progress"] == {"pct": 92, "next": 100, "step": "Scoring players"}


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


def test_fair_range_uses_model_band(scout, monkeypatch):
    monkeypatch.setitem(scout.MODEL_BAND, ("Men", "full"), (-0.5, 0.5))
    lo, hi = scout.fair_range(10_000_000, "Men", "full")
    assert lo == 6_065_000 and hi == 16_487_000  # 10M x e^-0.5 / e^0.5, rounded to £1k
    assert scout.fair_range(None, "Men", "full") is None and scout.fair_range(1, "Women", "nope") is None


def test_similar_order_scales_terms_per_mode(scout, monkeypatch):
    attrs = {f"attributes_{a}": 10 for a in scout._ATTR_TECH + scout._ATTR_MENTAL + scout._ATTR_PHYS}
    attrs.update(attributes_pace=19, attributes_acceleration=18, attributes_dribbling=17, attributes_finishing=16,
                 attributes_flair=15, attributes_agility=15)
    ref = {"stat_group": "ST", "natural_positions": "STC", "stat_score": 80.0, "s_avg_rating": 7.0, "s_goals_p90": 0.8,
           "s_xg_p90": None, "ability_current": 140, **attrs}
    monkeypatch.setattr(scout, "_player", lambda uid, mode: ref)
    monkeypatch.setattr(scout, "stat_strengths", lambda p: [("s_goals_p90", 0.8, 90)])  # his best position stat
    mb = scout.similar_order(1, "moneyball")
    assert "stat_score" in mb and "s_goals_p90" in mb and "s_xg_p90" not in mb and "attributes_" not in mb
    for mode in ("fair", "full"):  # his six strengths, nothing else (no CA, no average)
        sql = scout.similar_order(1, mode)
        assert sql.count("attributes_") == 6 and "attributes_pace" in sql and "attributes_flair" in sql
        assert "ability_current" not in sql and "attributes_corners" not in sql


def test_fast_json_matches_fastapi_default(scout):
    import datetime, math
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    body = {"total": 2, "rows": [{"a": 1, "b": 2.5, "c": "Zoë", "d": None, "e": True, "f": datetime.date(2026, 7, 1)},
                                 {"a": -3, "b": 1e-7, "c": "", "d": [1, 2], "e": False, "f": datetime.datetime(2026, 1, 2, 3, 4)}]}
    assert scout._json(body).body == JSONResponse(jsonable_encoder(body)).body
    with pytest.raises(ValueError):  # NaN is refused exactly like the default response
        scout._json({"x": math.nan})


def test_club_pick_when_save_names_no_club(scout, client, tmp_path, monkeypatch):
    monkeypatch.setattr(scout, "STATUS", "ready")
    monkeypatch.setattr(scout, "CLUB", None)
    (tmp_path / "data").mkdir()
    scout.con().execute("""CREATE OR REPLACE TABLE players AS SELECT * FROM (VALUES
        (1, 10, 'Chelsea'), (2, 10, 'Chelsea'), (3, 20, 'Chelsea U21'), (4, NULL, NULL)) t(uid, club_uid, club_name)""")
    assert client.get("/api/club").json()["detail"] == "no_managed_club"
    assert client.get("/api/clubs", params={"q": "chel"}).json() == [
        {"club_uid": 10, "club_name": "Chelsea", "players": 2}, {"club_uid": 20, "club_name": "Chelsea U21", "players": 1}]
    assert [c["club_uid"] for c in client.get("/api/clubs", params={"q": "20"}).json()] == [20]
    assert client.post("/api/club/pick", json={"club_uid": 99}).status_code == 404
    r = client.post("/api/club/pick", json={"club_uid": 20}).json()
    assert r["club_name"] == "Chelsea U21" and len(r["formation"]) == 11 and scout.CLUB == r
    assert json.loads((tmp_path / "data" / "club.json").read_text()) == r


def test_index_is_never_served_stale(client):
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_formation_falls_back_when_match_data_is_thin(scout):
    # Late saves (Hoffenheim, 2040): fmsave reads match rows for only 2 of 75 players, a few DM minutes a match.
    from types import SimpleNamespace as M
    import datetime
    rows = [M(position=M(raw=128), minutes=10, date=datetime.date(2040, 4, d)) for d in range(1, 6)]
    assert scout.infer_formation(rows) == scout.infer_formation([])
    full = [M(position=M(raw=raw), minutes=90, date=datetime.date(2040, 4, 1))
            for raw in (1, 4, 16, 16, 8, 1024, 1024, 1024, 2048, 4096, 16384)]
    assert scout.infer_formation(full) == ["GK", "DR", "DC", "DC", "DL", "MC", "MC", "MC", "AMR", "AML", "ST"]


def test_bad_formation_in_club_json_is_replaced(scout, tmp_path):
    (tmp_path / "club.json").write_text(json.dumps({"club_uid": 1, "club_name": "Hoffenheim", "formation": ["GK", "DM"]}))
    assert scout.read_club(tmp_path / "club.json")["formation"] == scout.infer_formation([])
    assert scout.read_club(tmp_path / "missing.json") is None


@pytest.fixture
def squad(scout, monkeypatch):
    """A tiny world: our club (uid 1) with one player per 4-2-3-1 slot plus a second striker, and outside players."""
    pos_cols = ["gk", "dc", "dl", "dr", "dm", "mc", "amc", "aml", "amr", "stc", "wbl", "wbr", "ml", "mr", "sw"]
    xi = ["gk", "dr", "dc", "dc", "dl", "dm", "dm", "amr", "amc", "aml", "stc"]
    rows = [(i + 1, 1, p, 60, 50) for i, p in enumerate(xi)] + [(12, 1, "stc", 55, 90)]  # 12: weaker striker, top performer
    rows += [(100 + i, 2, "stc", 70 + i, 40 + 10 * i) for i in range(3)]  # outside strikers: role score 70-72, performance 40-60
    con = scout.con()
    cols = ", ".join([f"CASE WHEN p = '{c}' THEN 20 ELSE 1 END AS positions_{c}" for c in pos_cols]
                     + [f"r AS role_{r}" for r in scout.ROLES])
    con.execute("CREATE OR REPLACE TABLE w (uid INT, club_uid INT, p VARCHAR, r DOUBLE, perf DOUBLE)")
    con.executemany("INSERT INTO w VALUES (?, ?, ?, ?, ?)", rows)
    deal = ", ".join(f"0.0 AS {k}_{m}" for k in scout.PER_MODE for m in scout.MODES)
    con.execute(f"""CREATE OR REPLACE TABLE players AS SELECT uid, 'P' || uid AS name, 25 AS age, 120 AS ability_current,
        140 AS ability_potential, 1000000 AS transfer_value, 1000 AS contract_wage, club_uid, 'Club ' || club_uid AS club_name,
        'Men' AS gender, false AS free_agent, perf AS stat_score, 1500 AS s_minutes, {cols}, {deal} FROM w""")
    cols = {r[0] for r in con.execute("DESCRIBE players").fetchall()}
    fair = {c for c in cols if c.startswith(scout.HIDDEN_PREFIXES)}
    hidden = {"full": set(), "fair": fair, "moneyball": fair | {c for c in cols if c.startswith(scout.MONEYBALL_PREFIXES)}}
    monkeypatch.setattr(scout, "COLS", cols, raising=False)  # set by load()
    monkeypatch.setattr(scout, "MODE_HIDDEN", hidden)
    monkeypatch.setattr(scout, "VISIBLE", {m: (cols - h - scout.INTERNAL) | set(scout.PER_MODE) for m, h in hidden.items()})
    monkeypatch.setattr(scout, "CLUB", {"club_uid": 1, "club_name": "Us", "formation": "GK DR DC DC DL DM DM AMR AMC AML ST".split()})
    monkeypatch.setattr(scout, "STATUS", "ready")
    return scout


def test_club_basis_switches_between_role_scores_and_performance(squad, client):
    st = lambda **p: client.get("/api/club", params=p).json()["slots"][10]["starter"]
    assert st()["uid"] == 11  # attributes: the 60 role score beats the 55
    assert st(basis="performance")["uid"] == 12 and st(basis="performance")["fit"] == 90
    assert st(mode="moneyball")["uid"] == 12  # moneyball is always performance
    assert client.get("/api/club", params={"basis": "vibes"}).status_code == 400


def test_club_pins_put_the_managers_pick_in_the_slot(squad, client):
    d = client.get("/api/club", params={"pins": "10:12"}).json()
    assert d["slots"][10]["starter"]["uid"] == 12 and d["slots"][10]["pinned"] and d["pins"] == {"10": 12}
    assert d["slots"][10]["backup"]["uid"] == 11
    # a pin the player can't play (a striker in goal) is dropped, not forced
    d = client.get("/api/club", params={"pins": "0:12"}).json()
    assert d["slots"][0]["starter"]["uid"] == 1 and d["pins"] == {}
    assert client.get("/api/club", params={"pins": "x"}).status_code == 400


def test_club_targets_follow_basis_and_edit_search(squad, client):
    q = {"slot_index": 10, "budget": None, "age_max": 40}
    uids = lambda **extra: [r["uid"] for r in client.post("/api/club/needs", json={**q, **extra}).json()["rows"]]
    assert uids() == [102, 101, 100]  # role score 72, 71, 70 over the starter's 60
    assert uids(basis="performance") == []  # nobody outside beats P12's 90 performance
    assert uids(basis="performance", pins={"10": 11}) == [102]  # with P11 (50) picked to start, the 60 is an upgrade
    # Edit search criteria narrow the list (here a role-score minimum), whatever positions they name
    assert uids(search={"filters": {"role_" + next(iter(squad.ROLES)): {"filterType": "number", "type": "greaterThanOrEqual", "filter": 71}},
                        "positions": ["GK"]}) == [102, 101]
