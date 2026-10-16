# FM26 Scout: one-click desktop app

## Goal
A non-technical player downloads one file from GitHub Releases, double-clicks it, and the scouting
interface opens in their browser. No terminal, Python, venv or pip. They pick a save from a list.

## Non-goals
Hosted/multi-user version, code signing, auto-update, installer wizard.

## Design

### 1. Launcher (`launcher.py`, new)
- Picks a free localhost port, starts uvicorn on `127.0.0.1` in a thread, opens the default browser
  on it once the server answers.
- Entry point for PyInstaller. Keeps the process alive while the server runs.

### 2. Data directory
- `app.py` reads/writes generated files (`all_world_players.csv`, `club_gender.csv`,
  `season_stats.csv`, `club.json`) in a user data dir instead of `HERE`:
  macOS `~/Library/Application Support/FM26 Scout`, Windows `%APPDATA%/FM26 Scout`,
  override with env `FM26_DATA_DIR`. Dev default stays the repo folder when the CSVs are there.
- `static/` and `nations.py` are bundled read-only resources (PyInstaller `--add-data`).

### 3. Empty start + save picker
- `load()` at import runs only if the data files exist. Otherwise endpoints that need `players`
  return 409 `{"detail": "no_save"}`.
- New `GET /api/saves`: lists `*.fm` in the default FM26 save folder (macOS
  `~/Library/Application Support/Sports Interactive/Football Manager 26/games`, Windows
  `~/Documents/Sports Interactive/Football Manager 26/games`), newest first, with name, size, mtime.
- New `POST /api/load-save {name}`: extract + load a save from that folder. `name` must be a bare
  filename ending `.fm` that exists in the folder (no path separators), else 400.
- Frontend: when `/api/meta` returns 409, show a welcome screen: the save list (click to load, with
  a "about 1 minute" progress message) and the existing file-picker as fallback.

### 4. Packaging
- `.github/workflows/release.yml`: on tag `v*`, build with PyInstaller on `macos-latest` and
  `windows-latest`, zip, attach to the GitHub Release.
- README rewritten: download, double-click, first-run warnings (macOS right-click > Open; Windows
  SmartScreen "More info" > "Run anyway"). Developer setup kept in a short section below.

## Risks
- Unsigned apps trigger OS warnings (documented in README).
- Build size ~150-250 MB.
- Windows build is only verified by CI; needs one manual test on a PC.
- PyInstaller may miss dynamic imports in `fmsave`/`duckdb`; fix with hidden-import flags.

## Testing
- Unit-level: `/api/load-save` rejects `../x.fm`, non-`.fm`, missing file.
- Manual: build the macOS app locally, launch, confirm browser opens, saves are listed, one loads,
  grid shows players; relaunch and confirm data reloads.
