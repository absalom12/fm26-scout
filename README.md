<img width="1512" height="826" alt="Capture d’écran 2026-10-16 à 12 35 38" src="https://github.com/user-attachments/assets/5ae00cb3-cfd1-405a-9079-e6c0a2369a64" />
In save data - Decembre 2028.

# FM26 Scout

Local web app to scout, value and shortlist players from a *Football Manager 26* save, read with
[`fmsave`](https://pypi.org/project/fmsave/) (read-only). FastAPI + DuckDB backend, single-page
frontend (AG Grid) in `static/index.html`.

Features: 75k-player grid with presets and an FM-style "Edit search", three data modes (Full /
Fair play: no CA/PA or hidden attributes / Moneyball: stats only), Deal Finder (fair fee and wage
vs actual), My Club (formation inferred from recent matches, squad power ranking, targets per
slot), Offers (fair-fee verdict and acceptance estimate), nationalities, men/women filter.

## Download (no install, no terminal)

1. Go to [Releases](https://github.com/absalom12/fm26-scout/releases/latest) and download **FM26-Scout-mac.zip** (Apple Silicon Macs) or **FM26-Scout-windows.zip**.
2. Unzip, then double-click **FM26-Scout** (Windows: `FM26-Scout.exe` inside the folder).
3. Your browser opens. Click your save in the list and wait about a minute. That's it.

The app closes by itself a few seconds after you close the browser tab.

**First launch warnings** (the app is not code-signed):
- **Mac:** it refuses to open. Open *System Settings → Privacy & Security*, scroll down and click **Open Anyway** next to "FM26-Scout", then confirm. Only needed once.
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

It's an interface dedicated to every type of recruitment style :
* CA / PA
* Player Attributes (no hidden attributes are displayed)
* Moneyball, only displaying in game statistics

All the available data of your save are in there and you can filter your way out to find the best possible player for your team.

Shoutouts to https://x.com/randal_olson for creating the fmsave python library, couldn't be possible without it.
Please check more of his work here https://github.com/rhiever.
