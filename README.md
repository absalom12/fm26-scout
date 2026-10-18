<img width="1512" height="825" alt="Capture d’écran 2026-10-18 à 01 33 15" src="https://github.com/user-attachments/assets/60973a26-b210-4c2d-83e5-6b84b06d4144" />
In save data - Decembre 2028.

# ⚽ FM26 Scout

A fast, local web application to scout, value, and shortlist players directly from your **Football Manager 2026** save file.

Powered by `fmsave` for read-only save file parsing, **FastAPI + DuckDB** for lightning-fast queries, and **AG Grid** for a responsive single-page frontend.

---

## 🔥 Key Features

- **75,000+ Player Grid:** Fast data exploration with FM-style "Edit Search" filters, column presets, and deep customization.
- **3 Flexible Scouting Modes:**
  - 🔓 **Full:** Unrestricted view including hidden **CA / PA** attributes and potential.
  - ⚖️ **Fair Play:** Realistic view—hidden attributes, **CA**, and **PA** are completely omitted.
  - 📊 **Moneyball:** Pure stats & observable in-game performance metrics without relying on raw attributes.
- **Deal Finder:** Evaluates fair transfer fees and wages versus actual club valuations to highlight genuine bargains.
- **My Club Engine:** Infer your team's tactical formation from recent matches, rank squad power, and receive slot-by-slot recruitment targets tailored to your tactical gaps.
- **Offers Validator:** Analyze incoming and outgoing bids with fair-fee verdicts and likelihood-of-acceptance estimates.
- **Comprehensive Database Filters:** Includes nationality filters, positional role weightings, and gender filters (strictly isolating men's database entries via the `UID < 2,000,000,000` rule).

---

## 🚀 Download & Quick Start (No Terminal / No Install)

Get up and running in less than two minutes without touching Python or the command line:

1. **Download:** Head over to **[Releases (v0.4.5)](https://github.com/absalom12/fm26-scout/releases/tag/v0.4.5)** and grab `FM26-Scout-mac.zip` (for Apple Silicon Macs) or `FM26-Scout-windows.zip`.
2. **Extract & Open:**
   - **macOS:** Unzip and drag `FM26-Scout` to your Applications folder (optional), then double-click it.
   - **Windows:** Extract the `FM26-Scout` folder anywhere (*do not run inside the ZIP archive*) and double-click `FM26-Scout.exe`.
3. **Scout:** Your default browser will automatically launch `http://127.0.0.1:8765` (the next free port if that one is taken). Select your `.fm` save file from the detected list and allow ~1 minute for parsing.

> ℹ️ **Auto-Exit & Local Security:** The background server automatically closes a few seconds after you close your browser tab. Your save file is read **100% locally** and never leaves your machine. The only online request is a check, at launch, for a newer version on GitHub (nothing about you or your save is sent).

---

### 🛡️ Is it safe to open?

- **Mac:** yes, and nothing to do. The app is made by a registered Apple developer and checked by Apple, so it opens like any other app.
- **Windows:** yes, but Windows will show a blue **"Windows protected your PC"** screen the first time. That is normal for small free apps from independent developers: Microsoft's seal of approval costs a yearly fee, which I'm not paying for a free project. To continue, click **More info**, then **Run anyway**. You only do this once.

The app runs only on your computer and never sends your save file anywhere. When it starts, it only asks GitHub whether a newer version exists, and shows a link if there is one. The code is public, so anyone can check it.

---

## 🛠️ For Developers & Contributors

If you want to run from source, modify code, or build executables manually:

```bash
# Clone repository
git clone https://github.com/absalom12/fm26-scout.git
cd fm26-scout

# Setup virtual environment (Python 3.12)
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# Run local development server
venv/bin/python launcher.py     # or: venv/bin/uvicorn app:app --port 8000

# Run unit tests
venv/bin/pytest

# Build standalone desktop executables (PyInstaller)
venv/bin/python build.py
```

# 📁 Application Storage

Generated databases and cached parse outputs live in:

macOS: ~/Library/Application Support/FM26 Scout/

Windows: %APPDATA%\FM26 Scout\

(Note: Set the FM26_DATA_DIR environment variable to override this storage path.)

# 🙏 Credits & Shoutouts

A massive shoutout to [Randal S. Olson](https://x.com/randal_olson) for creating the [fmsave](https://pypi.org/project/fmsave/) library. This project wouldn't be possible without his incredible work reverse-engineering Football Manager save files! Be sure to check out his other projects on [GitHub](https://github.com/rhiever).

# 📄 License

Distributed under the MIT License. See [LICENSE](https://github.com/absalom12/fm26-scout/blob/main/LICENSE.md) for details.

Bundled third-party files, in `static/vendor/`: [AG Grid Community](https://www.ag-grid.com/) 32.3.3 (MIT) and the Barlow, Barlow Condensed and JetBrains Mono fonts (SIL Open Font License 1.1). They ship with the app so it works offline and loads nothing from a CDN.
