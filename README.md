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

## Setup

```bash
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
```

## Data

The save's data is not in this repo. Generate it once from your save (about 1 minute):

```bash
venv/bin/python app.py "<path to your save.fm>"
```

This writes `all_world_players.csv`, `club_gender.csv`, `season_stats.csv` and `club.json` next to
`app.py`. Later you can refresh everything by dropping a newer `.fm` save on the page.

## Run

```bash
venv/bin/uvicorn app:app --port 8000
```

Open http://localhost:8000.

Shoutouts to https://x.com/randal_olson for creating the fmsave python library, couldn't be possible without it.
Please check more of his work here https://github.com/rhiever.
