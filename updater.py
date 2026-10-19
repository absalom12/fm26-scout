"""In-app update for the built app: download the latest release, check it, swap the app, relaunch.
Source runs keep the download link."""
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile, threading, urllib.request, zipfile

REPO = "absalom12/scoutsheet"
ASSET = {"darwin": "ScoutSheet-mac.zip", "win32": "ScoutSheet-windows.zip"}.get(sys.platform)
STATE = {"state": None, "pct": 0, "error": None}  # state: None | downloading | installing | restarting | error

# Windows can't move the folder of a running exe: this runs once the app has exited. Moves are retried while
# antivirus or WebView2 still hold a file; the old app is put back if the new one can't take its place.
SWAP_PS1 = r"""param($ProcId, $Cur, $New, $Target, $Work)
Wait-Process -Id $ProcId -ErrorAction SilentlyContinue
$old = Join-Path $Work 'old'
for ($i = 0; $i -lt 100 -and -not (Test-Path -LiteralPath $old); $i++) {
  try { Move-Item -LiteralPath $Cur -Destination $old -ErrorAction Stop } catch { Start-Sleep -Milliseconds 200 }
}
$app = $Cur
if (Test-Path -LiteralPath $old) {
  try { Move-Item -LiteralPath $New -Destination $Target -ErrorAction Stop; $app = $Target }
  catch { Move-Item -LiteralPath $old -Destination $Cur }
}
Start-Process -FilePath (Get-ChildItem -LiteralPath $app -Filter *.exe | Select-Object -First 1).FullName
Start-Sleep -Seconds 2
Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
"""


def bundle():
    """The running app (ScoutSheet.app on macOS, the folder holding ScoutSheet.exe on Windows), or None when this
    build can't replace itself."""
    if not getattr(sys, "frozen", False):
        return None
    exe = pathlib.Path(sys.executable).resolve()
    if sys.platform == "darwin":
        app = exe.parents[2]  # ScoutSheet.app/Contents/MacOS/ScoutSheet
        return app if app.suffix == ".app" else None
    return exe.parent if sys.platform == "win32" else None


def newer(latest, current):
    n = lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")]
    return n(latest) > n(current)


def latest():
    """(version, zip url, sha256 hex) of the latest GitHub release."""
    with urllib.request.urlopen(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=15) as r:
        rel = json.load(r)
    a = next(a for a in rel["assets"] if a["name"] == ASSET)
    if not a["browser_download_url"].startswith(f"https://github.com/{REPO}/releases/download/"):
        raise ValueError(f"unexpected asset url {a['browser_download_url']}")
    if not (a.get("digest") or "").startswith("sha256:"):
        raise ValueError("release has no checksum")
    return rel["tag_name"].lstrip("v"), a["browser_download_url"], a["digest"][7:]


def team(app):
    """Apple Team ID that signed `app`, or None when unsigned."""
    r = subprocess.run(["codesign", "-dv", "--verbose=2", str(app)], capture_output=True, text=True)
    t = next((l.split("=", 1)[1] for l in r.stderr.splitlines() if l.startswith("TeamIdentifier=")), None)
    return None if t in (None, "not set") else t


def install(current, url, sha256):
    """Download and check the update. macOS: swap the bundle now (a running app can be moved there). Windows: leave
    the new folder ready for swap_on_exit. Returns (new app path, work folder to delete after exit). The current app
    stays in place on any failure. The new app takes its own name (e.g. after a rename), next to the current one."""
    # Next to the app: the swap is a rename, so it must stay on one volume. Also fails early on a read-only folder
    # (Program Files, app run from the downloaded zip, App Translocation): the caller then shows the download link.
    work = pathlib.Path(tempfile.mkdtemp(dir=current.parent, prefix=".scoutsheet-update-"))
    try:
        STATE.update(state="downloading", pct=0)
        h = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=30) as r, open(work / "update.zip", "wb") as f:
            size, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(1 << 16):
                f.write(chunk); h.update(chunk); done += len(chunk)
                if size:
                    STATE["pct"] = min(99, done * 100 // size)
        if h.hexdigest() != sha256.lower():
            raise ValueError("download corrupted (checksum mismatch)")
        STATE.update(state="installing", pct=99)
        if sys.platform == "win32":
            with zipfile.ZipFile(work / "update.zip") as z:  # also leaves the files without the "from the Internet" tag
                z.extractall(work / "new")
            new = next(p for p in (work / "new").iterdir() if p.is_dir() and any(p.glob("*.exe")))
        else:
            subprocess.run(["ditto", "-x", "-k", work / "update.zip", work / "new"], check=True)  # keeps symlinks, xattrs
            new = next((work / "new").glob("*.app"))
            # The integrity check: the download must be signed by the same Apple developer as the running app.
            subprocess.run(["codesign", "--verify", "--deep", "--strict", new], check=True, capture_output=True)
            if not team(current) or team(new) != team(current):
                raise ValueError("update not signed by the same developer")
        target = current.parent / new.name
        if target != current and target.exists():
            raise ValueError(f"{target.name} already exists next to this app")
        if sys.platform == "win32":
            (work / "swap.ps1").write_text(SWAP_PS1, encoding="utf-8")
            return new, target, work
        current.rename(work / "old.app")
        try:
            new.rename(target)
        except OSError:
            (work / "old.app").rename(current)
            raise
        return None, target, work
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def relaunch(current, new, target, work):
    """Once this process has exited: macOS deletes the old bundle and opens the new one; Windows swaps the folders
    first (swap.ps1), then starts the new exe."""
    if sys.platform == "win32":
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                          "-File", str(work / "swap.ps1"), "-ProcId", str(os.getpid()), "-Cur", str(current),
                          "-New", str(new), "-Target", str(target), "-Work", str(work)],
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                         | subprocess.CREATE_NO_WINDOW)
        return
    subprocess.Popen(["/bin/sh", "-c", 'while kill -0 "$0" 2>/dev/null; do sleep .2; done; rm -rf "$1"; open "$2"',
                      str(os.getpid()), str(work), str(target)], start_new_session=True)


def quit_app():
    try:  # closing the window ends the launcher normally (server stopped, WebKit storage flushed)
        import webview
        for w in list(webview.windows):
            w.destroy()
    except Exception:
        pass
    threading.Timer(5, os._exit, (0,)).start()  # browser mode, or a window that would not close


def run(current_version):
    try:
        version, url, sha256 = latest()
        if not newer(version, current_version):
            raise ValueError("already up to date")
        app = bundle()
        new, target, work = install(app, url, sha256)
        STATE.update(state="restarting", pct=100)
        relaunch(app, new, target, work)
        quit_app()
    except Exception as e:
        STATE.update(state="error", error=str(e) or type(e).__name__)
