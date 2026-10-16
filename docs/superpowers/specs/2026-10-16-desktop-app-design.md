# FM26 Scout: one-click desktop app

## Goal
A non-technical player downloads one file from GitHub Releases, double-clicks it, and the scouting
interface opens in their browser. No terminal, Python, venv or pip. They pick a save from a list.

## Non-goals
Hosted/multi-user version, code signing, auto-update, installer wizard, several saves kept at once,
Intel Macs (arm64 build only), Linux.

## Design

### 1. Launcher (`launcher.py`, new; PyInstaller entry point)
- Fixed port 8765 (fallback: next free port). Fixed because the page keeps shortlist, searches and
  mode in `localStorage`, which is per origin (port included).
- If port 8765 already serves FM26 Scout (`GET /api/ping` answers), just open the browser on it and
  exit. No second server on the same data dir.
- Runs uvicorn on `127.0.0.1` only, `log_config=None` (no stdout under `--noconsole`), then opens the
  default browser.
- Auto-quit: the page calls `POST /api/ping` every 5 s; the server exits after 30 s without a ping
  (also covers a never-opened tab).

### 2. Data directory
- Generated files (`all_world_players.csv`, `club_gender.csv`, `season_stats.csv`, `club.json`) live in
  macOS `~/Library/Application Support/FM26 Scout`, Windows `%APPDATA%/FM26 Scout`; env `FM26_DATA_DIR`
  overrides. Replaces `HERE` in `load()` default and the `__main__` block of `app.py`.
- One save at a time: loading a save extracts into a temp dir inside the data dir, and only on success
  replaces the four files (so a failed or interrupted extraction keeps the previous data and no stale
  `club.json` survives a save without a managed club).
- `static/` is bundled read-only (`--add-data`); it is still served from `HERE`.

### 3. Empty start, background load, save picker
- `load()` no longer runs at import. At startup a background thread loads the data dir if the files exist
  (about 8 s); module state `STATUS` is `empty | loading | ready`.
- A single guard (dependency on all data routes) returns 409 `{"detail": "no_save" | "loading"}` instead of
  a DuckDB 500 or `KeyError`. `/api/meta`, `/api/status`, `/api/saves`, `/api/ping` are exempt where needed.
- `GET /api/saves`: `*.fm` files (not `config.xml`) in the default FM26 save folder, newest first
  (name, size, mtime), plus `{"folder_found": bool}`.
  macOS `~/Library/Application Support/Sports Interactive/Football Manager 26/games`;
  Windows `<Documents>/Sports Interactive/Football Manager 26/games` with `<Documents>` from
  `SHGetKnownFolderPath` (ctypes, handles OneDrive redirection). Exact Windows folder name: to verify.
- `POST /api/load-save {name}`: plain `def` (runs in the threadpool, the server stays responsive during the
  ~1 min extraction). `name` must equal `Path(name).name`, end with `.fm`, and resolve inside the saves
  folder after symlink resolution, else 400. `fmsave.FmsaveError` -> 400 with its message.
- Frontend: `init()` handles 409 (`no_save`/`loading`) with a welcome screen instead of the "start uvicorn"
  message: save list (click to load, "about 1 minute" progress), the existing file picker as fallback,
  a clear message if the folder is not found or extraction fails. The page pings `/api/ping`.

### 4. Packaging
- PyInstaller `--onedir` (not `--onefile`: no 200 MB re-extraction per launch, fewer antivirus false
  positives), `--noconsole`, `--collect-all fmsave` (bundles its `_layouts` data), hidden imports for
  uvicorn and `multipart`, Python 3.12.
- Step 1: get the macOS build working locally. Step 2: add `.github/workflows/release.yml` (on tag `v*`,
  `macos-latest` arm64 + `windows-latest`, macOS zipped with `ditto -c -k --keepParent` to keep the
  bundle valid, attached to the GitHub Release).
- README rewritten: download for your system, double-click. First-run warnings: macOS 15+ System
  Settings > Privacy & Security > "Open Anyway" (right-click > Open no longer works); Windows SmartScreen
  "More info" > "Run anyway". Apple Silicon Macs only. Developer setup stays in a short section.

## Risks
- Unsigned apps trigger OS warnings (documented in README).
- Build size ~150-250 MB.
- Windows build is only verified by CI; needs one manual test on a PC.
- PyInstaller may miss dynamic imports in `fmsave`/`duckdb`; fixed with hidden-import flags when found.

## Testing
- `/api/load-save` rejects `../x.fm`, a symlink pointing outside the folder, non-`.fm`, missing file.
- Guard returns 409 before any save is loaded; failed extraction keeps previous data.
- Manual: build the macOS app, launch twice (second launch reuses the first), confirm browser opens, saves
  are listed, one loads, grid shows players; relaunch reloads from the data dir; close the tab and confirm
  the process exits after ~30 s.
