# FM26 Scout One-Click Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A non-technical player downloads one file, double-clicks it, and the FM26 Scout interface opens in the browser with a list of their FM26 saves to load.

**Architecture:** `app.py` (FastAPI + DuckDB) gets an `empty | loading | ready` state, a single 409 guard, and save-picker endpoints; a new `datadir.py` owns paths and safe file handling; a new `launcher.py` starts uvicorn on a fixed port, opens the browser and exits when the tab is gone. PyInstaller (`--onedir --noconsole`) packages it; a GitHub Actions workflow builds macOS arm64 and Windows zips on a `v*` tag.

**Tech Stack:** Python 3.12, FastAPI, DuckDB, fmsave, uvicorn, PyInstaller, pytest + httpx (dev), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-16-desktop-app-design.md`

## Global Constraints

- Server binds `127.0.0.1` only, fixed port 8765 (fallback: next free port, up to +19).
- One save at a time; loading a save replaces the 4 data files (`all_world_players.csv`, `club_gender.csv`, `season_stats.csv`, `club.json`); a failed extraction keeps the previous data.
- Data dir: macOS `~/Library/Application Support/FM26 Scout`, Windows `%APPDATA%/FM26 Scout`; env `FM26_DATA_DIR` overrides. Saves dir env override: `FM26_SAVES_DIR`.
- Default saves folder: macOS `~/Library/Application Support/Sports Interactive/Football Manager 26/games`; Windows `<Documents>/Sports Interactive/Football Manager 26/games` (`<Documents>` via `SHGetFolderPathW`, CSIDL 5; folder name to verify on a real PC).
- `/api/load-save` name must equal `Path(name).name`, contain no backslash, end `.fm`, and resolve (symlinks followed) inside the saves folder, else 400.
- Data routes return 409 `{"detail": "no_save"}` (empty) or `{"detail": "loading"}` while not ready.
- PyInstaller: `--onedir --noconsole`, `--collect-all fmsave`, Python 3.12; macOS arm64 only, zipped with `ditto -c -k --keepParent`.
- Auto-quit: page pings `POST /api/ping` every 5 s and sends `/api/bye` on `pagehide`; server exits 15 s after a bye with no new ping, or after 600 s without any ping. (Refines the spec's "30 s without ping": background tabs throttle timers, a bye beacon is reliable.)
- Page text and README in English (existing language of the repo UI).

## Review Focus

- Save names with spaces, parentheses, dots (`Chantilly (v02).fm`, `Beta Lop - OL 2032.04.14.fm`) load normally. Pinned in Task 1.
- Default saves folder missing: welcome screen says so and offers the file picker, no crash. Pinned in Tasks 1 and 2.
- A file named `.fm` that is not a valid save: 400 with a readable message, previous data and status untouched. Pinned in Task 2.
- Clicking two saves quickly or loading during startup load: second request gets 409 `busy`, no corruption. Pinned in Task 2.
- Port 8765 taken by something that is not FM26 Scout: launcher picks the next free port instead of opening the wrong page. Pinned in Task 4.

---

### Task 1: `datadir.py` (paths and safe file handling)

**Files:**
- Create: `datadir.py`, `tests/test_datadir.py`, `pytest.ini`, `requirements-dev.txt`

**Interfaces:**
- Produces:
  - `FILES: tuple[str, ...]` the 4 data file names
  - `data_dir() -> Path` (created if missing)
  - `saves_dir() -> Path`
  - `list_saves(folder=None) -> list[dict] | None` (`name`, `size`, `mtime`, newest first; `None` if folder missing)
  - `resolve_save(name: str, folder=None) -> Path` (raises `ValueError`)
  - `replace_data(src, dst) -> None`

- [ ] **Step 1: Dev setup**

```bash
printf 'pytest\nhttpx\n' > /Users/thomaslopez/Football_Data/fm26data/requirements-dev.txt
printf '[pytest]\npythonpath = .\ntestpaths = tests\n' > /Users/thomaslopez/Football_Data/fm26data/pytest.ini
/Users/thomaslopez/Football_Data/fm26data/venv/bin/pip install -r /Users/thomaslopez/Football_Data/fm26data/requirements-dev.txt
```

- [ ] **Step 2: Write the failing tests** — `tests/test_datadir.py`

```python
import os
import pytest
from datadir import FILES, data_dir, list_saves, replace_data, resolve_save


def test_data_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FM26_DATA_DIR", str(tmp_path / "d"))
    assert data_dir() == tmp_path / "d" and data_dir().is_dir()


def test_list_saves_missing_folder_is_none(tmp_path):
    assert list_saves(tmp_path / "nope") is None


def test_list_saves_only_fm_newest_first(tmp_path):
    (tmp_path / "config.xml").write_text("x")
    a, b = tmp_path / "Chantilly (v02).fm", tmp_path / "Beta Lop - OL 2032.04.14.fm"
    a.write_bytes(b"1"); b.write_bytes(b"22")
    os.utime(a, (1000, 1000)); os.utime(b, (2000, 2000))
    assert [s["name"] for s in list_saves(tmp_path)] == [b.name, a.name]
    assert list_saves(tmp_path)[0]["size"] == 2


@pytest.mark.parametrize("name", ["Chantilly (v02).fm", "Beta Lop - OL 2032.04.14.fm", "é save.FM"])
def test_resolve_save_accepts_real_names(tmp_path, name):
    (tmp_path / name).write_bytes(b"x")
    assert resolve_save(name, tmp_path) == (tmp_path / name).resolve()


@pytest.mark.parametrize("name", ["", "../x.fm", "sub/x.fm", "..\\x.fm", "x.txt", "missing.fm"])
def test_resolve_save_rejects(tmp_path, name):
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(ValueError):
        resolve_save(name, tmp_path)


def test_resolve_save_rejects_symlink_outside(tmp_path):
    outside = tmp_path / "outside.fm"; outside.write_bytes(b"x")
    saves = tmp_path / "saves"; saves.mkdir()
    (saves / "link.fm").symlink_to(outside)
    with pytest.raises(ValueError):
        resolve_save("link.fm", saves)


def test_replace_data_swaps_and_removes_stale(tmp_path):
    dst, src = tmp_path / "dst", tmp_path / "dst" / "tmp"
    src.mkdir(parents=True)
    (dst / "club.json").write_text("stale")
    (dst / "all_world_players.csv").write_text("old")
    (src / "all_world_players.csv").write_text("new")
    replace_data(src, dst)
    assert (dst / "all_world_players.csv").read_text() == "new"
    assert not (dst / "club.json").exists()
```

- [ ] **Step 3: Run to verify failure**

Run: `/Users/thomaslopez/Football_Data/fm26data/venv/bin/pytest /Users/thomaslopez/Football_Data/fm26data/tests/test_datadir.py -q`
Expected: FAIL (`ModuleNotFoundError: datadir`).

- [ ] **Step 4: Implement** — `datadir.py`

```python
import os, sys
from pathlib import Path

FILES = ("all_world_players.csv", "club_gender.csv", "season_stats.csv", "club.json")


def data_dir() -> Path:
    if env := os.environ.get("FM26_DATA_DIR"):
        p = Path(env)
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "FM26 Scout"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "FM26 Scout"
    else:
        p = Path.home() / ".local" / "share" / "fm26-scout"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _documents_dir() -> Path:
    if sys.platform == "win32":  # follows OneDrive-redirected Documents
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
            return Path(buf.value)
    return Path.home() / "Documents"


def saves_dir() -> Path:
    if env := os.environ.get("FM26_SAVES_DIR"):
        return Path(env)
    base = Path.home() / "Library" / "Application Support" if sys.platform == "darwin" else _documents_dir()
    return base / "Sports Interactive" / "Football Manager 26" / "games"


def list_saves(folder=None):
    """`.fm` files newest first, or None if the folder does not exist."""
    folder = Path(folder) if folder else saves_dir()
    if not folder.is_dir():
        return None
    rows = []
    for p in folder.iterdir():
        if p.suffix.lower() == ".fm" and p.is_file():
            s = p.stat()
            rows.append({"name": p.name, "size": s.st_size, "mtime": s.st_mtime})
    return sorted(rows, key=lambda r: -r["mtime"])


def resolve_save(name, folder=None) -> Path:
    folder = Path(folder) if folder else saves_dir()
    if not name or Path(name).name != name or "\\" in name or not name.lower().endswith(".fm"):
        raise ValueError("bad save name")
    p = (folder / name).resolve()
    if p.parent != folder.resolve() or not p.is_file():
        raise ValueError("save not found")
    return p


def replace_data(src, dst) -> None:
    """Move freshly extracted files over the data files; a file the new save lacks is removed."""
    src, dst = Path(src), Path(dst)
    for f in FILES:
        if (src / f).exists():
            os.replace(src / f, dst / f)
        else:
            (dst / f).unlink(missing_ok=True)
```

- [ ] **Step 5: Run to verify pass**

Run: `/Users/thomaslopez/Football_Data/fm26data/venv/bin/pytest /Users/thomaslopez/Football_Data/fm26data/tests/test_datadir.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git -C /Users/thomaslopez/Football_Data/fm26data add datadir.py tests pytest.ini requirements-dev.txt
git -C /Users/thomaslopez/Football_Data/fm26data commit -m "feat: datadir module (data dir, saves listing, safe save resolution)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `app.py` state, 409 guard, save endpoints

**Files:**
- Modify: `app.py` (lines 1-10, `def load` ~361, `__main__`/`load()` ~438-442, `/api/upload` ~634-653, `index()` ~907)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `datadir` from Task 1.
- Produces:
  - `app.STATUS: str` (`"empty" | "loading" | "ready"`), `app._ingest_lock: threading.Lock`
  - `app.ingest(src) -> dict` (`{"rows", "game_date"}`; raises `HTTPException` 400/409)
  - `app.idle_expired(now=None, hard=600) -> bool`
  - routes: `GET /api/status` -> `{"app": "fm26-scout", "status": STATUS}`; `GET /api/saves` -> `{"folder_found", "saves"}`; `POST /api/load-save {name}`; `POST /api/ping`; `POST /api/bye`

- [ ] **Step 1: Write the failing tests** — `tests/test_api.py`

```python
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
    return TestClient(scout.app)  # not a context manager: startup loader does not run


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
        (__import__("pathlib").Path(out) / "all_world_players.csv").write_text("new")

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
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/thomaslopez/Football_Data/fm26data/venv/bin/pytest /Users/thomaslopez/Football_Data/fm26data/tests/test_api.py -q`
Expected: FAIL (`import app` runs `load()` against missing CSVs, or missing attributes).

- [ ] **Step 3: Edit `app.py` imports and app creation (lines 1-10)**

Replace line 1:
```python
import collections, contextlib, csv, io, json, os, re, shutil, sys, tempfile, threading, time, traceback
```
Replace the `from fastapi import ...` line with:
```python
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from datadir import FILES, data_dir, list_saves, replace_data, resolve_save
```
Replace `HERE = ...` and `app = FastAPI()` (lines 9-10) with:
```python
HERE = os.path.dirname(os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", HERE)  # bundled read-only resources (static/) when frozen

STATUS = "empty"  # empty | loading | ready
_ingest_lock = threading.Lock()  # one load at a time (startup load or a save load)
LAST_PING, BYE_AT = time.monotonic(), None
OPEN_PATHS = {"/", "/api/status", "/api/saves", "/api/load-save", "/api/upload", "/api/ping", "/api/bye"}


def need_data(request: Request):
    if STATUS != "ready" and request.url.path not in OPEN_PATHS:
        raise HTTPException(409, "no_save" if STATUS == "empty" else STATUS)


@contextlib.asynccontextmanager
async def lifespan(_):
    threading.Thread(target=_load_data_dir, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan, dependencies=[Depends(need_data)])
```

- [ ] **Step 4: Edit `load` signature (~line 361)**

Replace
```python
def load(d=HERE):
    global GAME_DATE
```
with
```python
def load(d=None):
    global GAME_DATE
    d = str(d or data_dir())
```

- [ ] **Step 5: Replace the `__main__` block and the bare `load()` (~lines 438-442)**

Replace
```python
if __name__ == "__main__":  # python app.py <save.fm>: write the players CSV (if missing) and the sidecars
    extract(sys.argv[1], HERE, players=not os.path.exists(os.path.join(HERE, "all_world_players.csv")))
    sys.exit()

load()
```
with
```python
if __name__ == "__main__":  # python app.py <save.fm>: write the data files into the data dir
    extract(sys.argv[1], str(data_dir()))
    sys.exit()


def _load_data_dir():
    """Startup: load the data dir in the background if a previous save was extracted there."""
    global STATUS
    if not (data_dir() / FILES[0]).exists():
        return
    with _ingest_lock:
        STATUS = "loading"
        try:
            load()
            STATUS = "ready"
        except Exception:
            traceback.print_exc()
            STATUS = "empty"
```

- [ ] **Step 6: Replace `/api/upload` (the whole `async def upload` function) with ingest + new routes**

```python
def ingest(src):
    """Extract a .fm save and make it the only loaded data. One at a time; failures keep the old data."""
    global STATUS
    if not _ingest_lock.acquire(blocking=False):
        raise HTTPException(409, "busy")
    try:
        dd = data_dir()
        tmp = tempfile.mkdtemp(dir=dd)
        try:
            extract(str(src), tmp)
            replace_data(tmp, dd)
        except fmsave.FmsaveError as e:
            raise HTTPException(400, str(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        STATUS = "loading"
        try:
            load(dd)
        except Exception:
            STATUS = "empty"
            raise
        STATUS = "ready"
        return {"rows": con().execute("SELECT count(*) FROM players").fetchone()[0], "game_date": GAME_DATE}
    finally:
        _ingest_lock.release()


@app.post("/api/upload")
def upload(file: UploadFile = File(...)):  # fallback picker: the browser sends the file bytes
    if not (file.filename or "").lower().endswith(".fm"):
        raise HTTPException(400, "need .fm save")
    with tempfile.TemporaryDirectory(dir=data_dir()) as d:
        src = os.path.join(d, "s.fm")
        with open(src, "wb") as f:
            shutil.copyfileobj(file.file, f)
        return ingest(src)


class SaveReq(BaseModel):
    name: str


@app.post("/api/load-save")
def load_save(q: SaveReq):
    try:
        src = resolve_save(q.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ingest(src)


@app.get("/api/status")
def status():
    return {"app": "fm26-scout", "status": STATUS}


@app.get("/api/saves")
def saves():
    s = list_saves()
    return {"folder_found": s is not None, "saves": s or []}


@app.post("/api/ping")
def ping():
    global LAST_PING, BYE_AT
    LAST_PING, BYE_AT = time.monotonic(), None
    return {"ok": True}


@app.post("/api/bye")  # sent by the page on pagehide; a reload pings again within the grace period
def bye():
    global BYE_AT
    BYE_AT = time.monotonic() + 15
    return {"ok": True}


def idle_expired(now=None, hard=600):
    now = time.monotonic() if now is None else now
    return (BYE_AT is not None and now > BYE_AT) or now - LAST_PING > hard
```

- [ ] **Step 7: Edit `index()` (last lines)**

Replace `os.path.join(HERE, "static", "index.html")` with `os.path.join(RES, "static", "index.html")`.

- [ ] **Step 8: Run tests**

Run: `/Users/thomaslopez/Football_Data/fm26data/venv/bin/pytest /Users/thomaslopez/Football_Data/fm26data/tests -q`
Expected: all PASS.

- [ ] **Step 9: Real-data smoke test (uses the user's real save)**

```bash
FM26_DATA_DIR=/private/tmp/claude-501/fm26smoke /Users/thomaslopez/Football_Data/fm26data/venv/bin/uvicorn app:app --app-dir /Users/thomaslopez/Football_Data/fm26data --port 8765 &
sleep 4; curl -s localhost:8765/api/status; curl -s localhost:8765/api/saves | head -c 300
curl -s -X POST localhost:8765/api/load-save -H 'Content-Type: application/json' -d '{"name":"Chantilly.fm"}'
curl -s localhost:8765/api/meta | head -c 100; kill %1
```
Expected: status `empty`, saves list includes `Chantilly.fm`, load-save returns `{"rows":...,"game_date":...}` after ~1 min, meta returns JSON.

- [ ] **Step 10: Commit**

```bash
git -C /Users/thomaslopez/Football_Data/fm26data add app.py tests
git -C /Users/thomaslopez/Football_Data/fm26data commit -m "feat: empty/loading/ready state, 409 guard, save list and load-save endpoints" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Frontend welcome screen and ping

**Files:**
- Modify: `static/index.html` (`upload()` ~1499-1520, `init()` ~1523-1526, end of script)

**Interfaces:**
- Consumes: `/api/meta` (409 when no data), `/api/status`, `/api/saves`, `/api/load-save`, `/api/upload`, `/api/ping`, `/api/bye` from Task 2; existing `emptyState(title, text, extra)`, `toast`, `api`, `$`, `esc`, `sleep`.

- [ ] **Step 1: Replace the `upload` function block** (from `let uploading = false;` through the closing `}` of `upload`) with:

```js
let uploading = false, EMPTY = false;
async function runLoad(doFetch) {
  if (uploading) return;
  uploading = true;
  const btn = $('#loadBtn'), t0 = Date.now();
  btn.disabled = true; $('#progress').classList.add('on');
  const tick = () => { btn.innerHTML = `<span class="spin" aria-hidden="true"></span><span>Extracting… ${Math.round((Date.now() - t0) / 1000)}s</span>`; };
  tick(); const iv = setInterval(tick, 1000);
  toast('Extracting players from your save. This usually takes 40 to 60 seconds.', '', 6000);
  try {
    const r = await doFetch();
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail === 'busy' ? 'Another save is already loading.' : d.detail || 'Upload failed');
    if (EMPTY) return location.reload();
    META.game_date = d.game_date; peerCache.clear();
    toast(`Loaded ${Number(d.rows).toLocaleString()} players · game date ${d.game_date}`, 'good', 5000);
    refresh(); renderSummary();
    if (CLUB) { CLUB = null; if (st.page === 'club') loadClub(); }
  } catch (e) { toast('Upload failed: ' + e.message, 'bad', 8000); }
  finally { clearInterval(iv); uploading = false; btn.disabled = false; btn.innerHTML = '<span>Load save</span>'; $('#progress').classList.remove('on'); $('#file').value = ''; }
}
function upload(f) {
  if (!f) return;
  if (!/\.fm$/i.test(f.name)) return toast('That is not a .fm save file.', 'bad');
  const fd = new FormData(); fd.append('file', f);
  return runLoad(() => fetch('/api/upload', { method: 'POST', body: fd }));
}
const loadNamed = name => runLoad(() => fetch('/api/load-save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }));

async function showWelcome() {
  $('#grid').innerHTML = emptyState('Load your save', 'Pick a Football Manager 26 save to start scouting. Extraction takes about a minute.', '<div id="saves" style="display:flex;flex-direction:column;gap:8px;align-items:center;margin-top:12px"></div>');
  let d = { folder_found: false, saves: [] };
  try { d = await (await api('/api/saves')).json(); } catch {}
  const box = $('#saves');
  box.innerHTML = d.saves.length
    ? d.saves.map(s => `<button class="btn" data-save="${esc(s.name)}">${esc(s.name)} · ${Math.round(s.size / 1048576)} MB · ${new Date(s.mtime * 1000).toLocaleString()}</button>`).join('')
    : `<p>${d.folder_found ? 'No .fm save found in the default FM26 folder.' : 'FM26 save folder not found.'} Use “Load save” (top right) to pick your .fm file.</p>`;
  box.onclick = e => { const b = e.target.closest('[data-save]'); if (b) loadNamed(b.dataset.save); };
}
async function waitReady() {
  $('#grid').innerHTML = emptyState('Loading your data…', 'This takes a few seconds.');
  while ((await (await api('/api/status')).json()).status === 'loading') await sleep(1000);
  location.reload();
}
```

- [ ] **Step 2: Edit `init()`** — replace the line starting `if (!r || !r.ok) { $('#grid').innerHTML = emptyState('Cannot reach the server', ...` with:

```js
  if (r && r.status === 409) { EMPTY = true; const d = await r.json().catch(() => ({})); return d.detail === 'loading' ? waitReady() : showWelcome(); }
  if (!r || !r.ok) { $('#grid').innerHTML = emptyState('Cannot reach the server', 'Close this tab, open FM26 Scout again, then reload.', '<button class="btn" onclick="location.reload()">Reload</button>'); return; }
```

- [ ] **Step 3: Add the keep-alive** just before the existing `init()` call at the end of the script (find it with `grep -n '^init()' static/index.html`):

```js
const ping = () => fetch('/api/ping', { method: 'POST' }).catch(() => {});
ping(); setInterval(ping, 5000);
addEventListener('pagehide', () => navigator.sendBeacon('/api/bye'));
```

- [ ] **Step 4: Manual check in the browser (Claude in Chrome or by hand)**

Run the Task 2 step 9 server with an empty `FM26_DATA_DIR`. Open `http://localhost:8765`. Expected: "Load your save" screen listing the real saves; click one, spinner shows, page reloads into the grid with players. Reload the page again: grid still loads. Stop the server, start it again with the same data dir: grid loads after a few seconds ("Loading your data…" may flash).

- [ ] **Step 5: Commit**

```bash
git -C /Users/thomaslopez/Football_Data/fm26data add static/index.html
git -C /Users/thomaslopez/Football_Data/fm26data commit -m "feat: welcome screen with save list, keep-alive ping" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `launcher.py`

**Files:**
- Create: `launcher.py`, `tests/test_launcher.py`

**Interfaces:**
- Consumes: `app.app`, `app.idle_expired`, `GET /api/status` from Task 2.
- Produces: `launcher.PORT = 8765`, `is_scout(port) -> bool`, `pick_port(start=PORT) -> int`, `main() -> None` (PyInstaller entry point).

- [ ] **Step 1: Write the failing tests** — `tests/test_launcher.py`

```python
import socket
from launcher import is_scout, pick_port


def test_pick_port_skips_taken_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); taken = s.getsockname()[1]
    try:
        assert pick_port(taken) != taken
    finally:
        s.close()


def test_is_scout_false_on_foreign_listener():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    try:
        assert not is_scout(s.getsockname()[1])
    finally:
        s.close()


def test_is_scout_false_when_nothing_listens():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    assert not is_scout(port)
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/thomaslopez/Football_Data/fm26data/venv/bin/pytest /Users/thomaslopez/Football_Data/fm26data/tests/test_launcher.py -q`
Expected: FAIL (`ModuleNotFoundError: launcher`).

- [ ] **Step 3: Implement** — `launcher.py`

```python
import json, socket, threading, time, urllib.request, webbrowser

import uvicorn

import app as scout

PORT = 8765  # fixed: the page keeps shortlist/searches in localStorage, which is per origin (port included)


def is_scout(port) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as r:
            return json.load(r).get("app") == "fm26-scout"
    except Exception:
        return False


def _free(port) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(start=PORT) -> int:
    for p in range(start, start + 20):
        if _free(p):
            return p
    raise RuntimeError("no free port")


def main():
    if is_scout(PORT):  # already running: just show it
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    port = pick_port()
    server = uvicorn.Server(uvicorn.Config(scout.app, host="127.0.0.1", port=port, log_config=None))

    def watch():  # quit once the tab is gone
        while not server.should_exit:
            time.sleep(2)
            if scout.idle_expired():
                server.should_exit = True

    def open_when_up():
        while not is_scout(port):
            time.sleep(0.3)
        webbrowser.open(f"http://127.0.0.1:{port}")

    threading.Thread(target=watch, daemon=True).start()
    threading.Thread(target=open_when_up, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `/Users/thomaslopez/Football_Data/fm26data/venv/bin/pytest /Users/thomaslopez/Football_Data/fm26data/tests -q`
Expected: all PASS.

- [ ] **Step 5: Manual run from source**

Run: `FM26_DATA_DIR=/private/tmp/claude-501/fm26smoke /Users/thomaslopez/Football_Data/fm26data/venv/bin/python /Users/thomaslopez/Football_Data/fm26data/launcher.py`
Expected: browser opens on 8765. Run it a second time in another terminal: it opens the browser and exits at once. Close the tab: the first process exits within ~20 s.

- [ ] **Step 6: Commit**

```bash
git -C /Users/thomaslopez/Football_Data/fm26data add launcher.py tests/test_launcher.py
git -C /Users/thomaslopez/Football_Data/fm26data commit -m "feat: launcher (fixed port, single instance, auto-quit)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: PyInstaller build, verified locally on macOS

**Files:**
- Create: `build.py`
- Modify: `requirements-dev.txt`, `.gitignore`

**Interfaces:**
- Consumes: `launcher.py` from Task 4, `static/`.
- Produces: `dist/FM26-Scout.app` (macOS) / `dist/FM26-Scout/` (Windows) via `python build.py`.

- [ ] **Step 1: Install and pin PyInstaller**

```bash
/Users/thomaslopez/Football_Data/fm26data/venv/bin/pip install pyinstaller
/Users/thomaslopez/Football_Data/fm26data/venv/bin/pip freeze | grep -i '^pyinstaller=='
```
Append the printed `pyinstaller==X.Y.Z` line to `requirements-dev.txt`. Append `build/` and `dist/` and `*.spec` to `.gitignore`.

- [ ] **Step 2: Create `build.py`**

```python
import sys

import PyInstaller.__main__

sep = ";" if sys.platform == "win32" else ":"
PyInstaller.__main__.run([
    "launcher.py", "--name", "FM26-Scout", "--noconsole", "--onedir", "--noconfirm", "--clean",
    "--add-data", f"static{sep}static",
    "--collect-all", "fmsave",
    "--collect-submodules", "uvicorn",
    "--hidden-import", "multipart",
])
```

- [ ] **Step 3: Build**

Run: `/Users/thomaslopez/Football_Data/fm26data/venv/bin/python /Users/thomaslopez/Football_Data/fm26data/build.py 2>&1 | tail -15`
Expected: ends with `Building BUNDLE ... completed successfully`; `dist/FM26-Scout.app` exists.

- [ ] **Step 4: Run the bundled app against the real save**

Run: `open /Users/thomaslopez/Football_Data/fm26data/dist/FM26-Scout.app` (use a clean data dir: `rm -rf "$HOME/Library/Application Support/FM26 Scout"` first, the user confirms this folder holds nothing but data from this app).
Check: browser opens on 8765; welcome screen lists saves; load `Chantilly.fm`; grid shows players; close the tab, the process ends after ~20 s (`pgrep -f FM26-Scout` empty); reopen the app: grid loads without asking for a save.
If the app dies at launch with `ModuleNotFoundError: X` or a missing-data error, run the executable directly (`dist/FM26-Scout.app/Contents/MacOS/FM26-Scout`) to read the traceback, then add `--hidden-import X` or `--collect-all X` (likely candidates: `duckdb`, `numpy`) to `build.py`, rebuild, repeat.

- [ ] **Step 5: Commit**

```bash
git -C /Users/thomaslopez/Football_Data/fm26data add build.py requirements-dev.txt .gitignore
git -C /Users/thomaslopez/Football_Data/fm26data commit -m "build: PyInstaller script" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Release workflow and README

**Files:**
- Create: `.github/workflows/release.yml`
- Modify: `README.md`

- [ ] **Step 1: Create `.github/workflows/release.yml`**

```yaml
name: release
on:
  push:
    tags: ["v*"]
permissions:
  contents: write
jobs:
  build:
    strategy:
      matrix:
        include:
          - os: macos-latest
            asset: FM26-Scout-mac.zip
          - os: windows-latest
            asset: FM26-Scout-windows.zip
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: python build.py
      - if: runner.os == 'macOS'
        run: ditto -c -k --keepParent dist/FM26-Scout.app ${{ matrix.asset }}
      - if: runner.os == 'Windows'
        run: Compress-Archive -Path dist/FM26-Scout -DestinationPath ${{ matrix.asset }}
      - uses: softprops/action-gh-release@v2
        with: { files: "${{ matrix.asset }}" }
```

- [ ] **Step 2: Rewrite `README.md`**

Keep the existing screenshot line, the "In save data" line and the shoutout paragraph. Replace the "Setup / Data / Run" sections with a "Download" section placed right after the intro:

```markdown
## Download (no install, no terminal)

1. Go to [Releases](https://github.com/absalom12/fm26-scout/releases/latest) and download **FM26-Scout-mac.zip** (Apple Silicon Macs) or **FM26-Scout-windows.zip**.
2. Unzip, then double-click **FM26-Scout** (Windows: `FM26-Scout.exe` inside the folder).
3. Your browser opens. Click your save in the list and wait about a minute. That's it.

The app closes by itself a few seconds after you close the browser tab.

**First launch warnings** (the app is not code-signed):
- **Mac:** it will refuse to open. Open *System Settings → Privacy & Security*, scroll down and click **Open Anyway** next to "FM26-Scout", then confirm. Only needed once.
- **Windows:** SmartScreen says "Windows protected your PC". Click **More info → Run anyway**.

Your save is read locally and never leaves your computer. Loading another save replaces the previous one.

## For developers

```bash
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt -r requirements-dev.txt
venv/bin/python launcher.py     # or: venv/bin/uvicorn app:app --port 8000
venv/bin/pytest
venv/bin/python build.py        # builds dist/ with PyInstaller
```

Generated data lives in `~/Library/Application Support/FM26 Scout` (macOS) or `%APPDATA%/FM26 Scout` (Windows); set `FM26_DATA_DIR` to change it.
```

- [ ] **Step 3: Validate the workflow file**

Run: `/Users/thomaslopez/Football_Data/fm26data/venv/bin/python -c "import yaml,sys; yaml.safe_load(open('/Users/thomaslopez/Football_Data/fm26data/.github/workflows/release.yml'))"` (if `yaml` is missing: `pip install pyyaml` first).
Expected: no error.

- [ ] **Step 4: Commit and push (ask the user before pushing and before tagging)**

```bash
git -C /Users/thomaslopez/Football_Data/fm26data add .github README.md
git -C /Users/thomaslopez/Football_Data/fm26data commit -m "ci: release workflow; README for one-click install" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
Then, only with the user's go-ahead: `git push`, `git tag v0.1.0 && git push origin v0.1.0`, watch the Actions run (`gh run watch`), download both zips from the Release, and ask the user (or a friend) to test the Windows zip on a PC.

## Self-Review

- Spec coverage: launcher/port/single instance/auto-quit (Task 4, plus ping/bye in Tasks 2-3); data dir + atomic replace + no stale `club.json` (Tasks 1-2); empty start, background load, 409 guard, `/api/saves`, `/api/load-save`, welcome screen, error messages (Tasks 2-3); `--onedir`, `--noconsole`, `--collect-all fmsave`, hidden imports, ditto zip, README warnings, Mac-first then CI (Tasks 5-6). The only intentional deviation from the spec is the auto-quit mechanism (bye beacon + 600 s hard timeout instead of 30 s without ping), noted in Global Constraints.
- Types: `ingest`, `STATUS`, `_ingest_lock`, `idle_expired`, `resolve_save`, `replace_data`, `list_saves`, `FILES` are named identically in every task that uses them.
- Known unknowns left to execution: exact PyInstaller hidden imports (Task 5 step 4 loop), Windows folder name and Windows build (CI + one PC test).
