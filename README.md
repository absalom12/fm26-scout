<img width="1512" height="947" alt="Capture d’écran 2026-10-19 à 13 17 18" src="https://github.com/user-attachments/assets/9aa959e8-2fcd-4b89-bc43-49831716bef2" />

# ⚽ ScoutSheet

**Sign the stats. Not the name.** A free scouting tool for Football Manager 26.

ScoutSheet pulls every player out of your own FM26 save and puts them in one place. Scout them in the app, Moneyball style if you like, or export the data and dig in with your own tools. Find the players who are better than their price.

Free and open source. Runs on your computer, works offline, and never writes to your save.

🌐 **[scoutsheet.fm](https://scoutsheet.fm)**

---

## Three ways to scout

One click switches modes. Same save, same players, different eyes.

- **Full:** the whole truth. Current and potential ability, hidden attributes, personality, and a written scouting report on every player.
- **Fair play:** find them yourself. Only what your scouts would tell you in the game: no potential, no hidden attributes.
- **Moneyball:** only results count. Attributes switched off; players are judged on what they do in matches, next to what they cost.

## What you get

- **Your weak spots:** ScoutSheet rates every position in your team and shows the weakest first. Click one for the players who would make it better, with what they cost.
- **Player profile:** where he plays, what he's good at, how he compares with players in his position, and in Moneyball, how well his season stats fit each role. Stats, price, contract and similar players are one tab away.
- **Bargains:** every player gets a fair price, worked out from similar players (same kind of profile, age, level and contract) in your own save. Then you see who sells for far less than he's worth. In Moneyball, a player needs 900 minutes before he gets one.
- **Your data, anywhere:** export any list, with every column, to a CSV file for Excel, Google Sheets or your own tools.
- **Offers:** check a bid against the player's fair price before you accept or send it.
- **Men and women:** every player in the save, with a switch between them.

---

## 🚀 Download and first launch

Get the latest version from **[Releases](https://github.com/absalom12/scoutsheetfm/releases/latest)**: `ScoutSheet-windows.zip` or `ScoutSheet-mac.zip` (Apple Silicon, M1 or later). Ready in about three minutes.

- **Windows:** unzip the folder first (don't run it from inside the zip), then double-click `ScoutSheet.exe`. If Windows says it protected your PC, click **More info**, then **Run anyway**. That's the only time you'll see it.
- **Mac:** unzip, drag ScoutSheet to Applications, then double-click it.

The app opens in its own window. Pick your save: reading it takes about a minute. When a new version is out, one click installs it and restarts the app.

### 🛡️ Is it safe to open?

- **Mac:** yes, and nothing to do. The app is signed and notarised by Apple, so it opens like any other app.
- **Windows:** yes, but SmartScreen asks you to confirm once. Microsoft's signing costs a yearly fee, which a free project doesn't pay.

Your save is only read, never written, and never leaves your computer. The one online request: at launch, the app asks GitHub whether a newer version exists (nothing about you or your save is sent; you can turn the check off in Settings). The code is public, so anyone can check it.

---

## 🛠️ For developers

Built on `fmsave` (read-only save parsing), **FastAPI + DuckDB** for queries and **AG Grid** for the single-page frontend.

```bash
git clone https://github.com/absalom12/scoutsheetfm.git
cd scoutsheetfm

# Python 3.12
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt -r requirements-dev.txt

venv/bin/python launcher.py     # or: venv/bin/uvicorn app:app --port 8000
venv/bin/pytest
venv/bin/python build.py        # standalone app (PyInstaller)
```

Releases are built by GitHub Actions on a `v*` tag.

# 📁 Application Storage

Generated databases and cached parse outputs live in:

macOS: ~/Library/Application Support/ScoutSheet/

Windows: %APPDATA%\ScoutSheet\

(The app used to be called FM26 Scout: its old `FM26 Scout` folder is moved there on first launch, so you don't have to load your save again.)

(Note: Set the SCOUTSHEET_DATA_DIR environment variable to override this storage path.)

# 🙏 Credits & Shoutouts

A massive shoutout to [Randal S. Olson](https://x.com/randal_olson) for creating the [fmsave](https://pypi.org/project/fmsave/) library. This project wouldn't be possible without his incredible work reverse-engineering Football Manager save files! Be sure to check out his other projects on [GitHub](https://github.com/rhiever).

# 📄 License

Distributed under the MIT License. See [LICENSE](https://github.com/absalom12/scoutsheetfm/blob/main/LICENSE.md) for details.

Bundled third-party files, in `static/vendor/`: [AG Grid Community](https://www.ag-grid.com/) 32.3.3 (MIT) and the Barlow, Barlow Condensed and JetBrains Mono fonts (SIL Open Font License 1.1). They ship with the app so it works offline and loads nothing from a CDN.
