import os, sys
from pathlib import Path

FILES = ("all_world_players.csv", "club_gender.csv", "club_league.csv", "season_stats.csv", "club.json")


def data_dir() -> Path:
    if env := os.environ.get("SCOUTSHEET_DATA_DIR"):
        p = Path(env)
    else:
        if sys.platform == "win32":
            base, name, old = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"), "ScoutSheet", "FM26 Scout"
        elif sys.platform == "darwin":
            base, name, old = Path.home() / "Library" / "Application Support", "ScoutSheet", "FM26 Scout"
        else:
            base, name, old = Path.home() / ".local" / "share", "scoutsheet", "fm26-scout"
        p = base / name
        if not p.exists() and (base / old).is_dir():  # the app was called FM26 Scout: keep the user's data
            try:
                (base / old).rename(p)
            except OSError:  # the old app is still running (Windows): use its folder this time, move it next launch
                p = base / old
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
    if env := os.environ.get("SCOUTSHEET_SAVES_DIR"):
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
