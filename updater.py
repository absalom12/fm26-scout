"""In-app update for the macOS app: download the latest release, check its Apple signature, swap the bundle, relaunch.
Windows and source runs keep the download link."""
import json, os, pathlib, shutil, subprocess, sys, tempfile, threading, urllib.request

REPO = "absalom12/fm26-scout"
ASSET = "FM26-Scout-mac.zip"
STATE = {"state": None, "pct": 0, "error": None}  # state: None | downloading | installing | restarting | error


def bundle():
    """The running FM26-Scout.app, or None when this build can't replace itself."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    app = pathlib.Path(sys.executable).resolve().parents[2]  # FM26-Scout.app/Contents/MacOS/FM26-Scout
    return app if app.suffix == ".app" else None


def newer(latest, current):
    n = lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")]
    return n(latest) > n(current)


def latest():
    """(version, zip url) of the latest GitHub release."""
    with urllib.request.urlopen(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=15) as r:
        rel = json.load(r)
    url = next(a["browser_download_url"] for a in rel["assets"] if a["name"] == ASSET)
    if not url.startswith(f"https://github.com/{REPO}/releases/download/"):
        raise ValueError(f"unexpected asset url {url}")
    return rel["tag_name"].lstrip("v"), url


def team(app):
    """Apple Team ID that signed `app`, or None when unsigned."""
    r = subprocess.run(["codesign", "-dv", "--verbose=2", str(app)], capture_output=True, text=True)
    t = next((l.split("=", 1)[1] for l in r.stderr.splitlines() if l.startswith("TeamIdentifier=")), None)
    return None if t in (None, "not set") else t


def install(current, url):
    """Replace the `current` bundle with the one in the zip at `url`; return the folder holding the old bundle
    (still in use by this process: delete it after exit). The current app stays in place on any failure."""
    # Next to the app: the swap is a rename, so it must stay on one volume. Also fails early on a read-only folder
    # (app run from the downloaded zip, App Translocation): the caller then shows the download link.
    work = pathlib.Path(tempfile.mkdtemp(dir=current.parent, prefix=".fm26-update-"))
    try:
        STATE.update(state="downloading", pct=0)
        with urllib.request.urlopen(url, timeout=30) as r, open(work / "update.zip", "wb") as f:
            size, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(1 << 16):
                f.write(chunk); done += len(chunk)
                if size:
                    STATE["pct"] = min(99, done * 100 // size)
        STATE.update(state="installing", pct=99)
        subprocess.run(["ditto", "-x", "-k", work / "update.zip", work / "new"], check=True)  # keeps symlinks, xattrs
        new = next((work / "new").glob("*.app"))
        # The integrity check: the download must be signed by the same Apple developer as the running app.
        subprocess.run(["codesign", "--verify", "--deep", "--strict", new], check=True, capture_output=True)
        if not team(current) or team(new) != team(current):
            raise ValueError("update not signed by the same developer")
        current.rename(work / "old.app")
        try:
            new.rename(current)
        except OSError:
            (work / "old.app").rename(current)
            raise
        return work
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def relaunch(app, work):
    """Once this process has exited: delete the old bundle, open the new one."""
    subprocess.Popen(["/bin/sh", "-c", 'while kill -0 "$0" 2>/dev/null; do sleep .2; done; rm -rf "$1"; open "$2"',
                      str(os.getpid()), str(work), str(app)], start_new_session=True)


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
        version, url = latest()
        if not newer(version, current_version):
            raise ValueError("already up to date")
        app = bundle()
        work = install(app, url)
        STATE.update(state="restarting", pct=100)
        relaunch(app, work)
        quit_app()
    except Exception as e:
        STATE.update(state="error", error=str(e) or type(e).__name__)
