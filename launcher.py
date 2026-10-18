import json, os, socket, sys, threading, time, traceback, urllib.request, webbrowser

from datadir import data_dir

PORT = 8765  # fixed: the page keeps shortlist/searches in localStorage, which is per origin (port included)

# Shown at once while the server starts. On the first launch of a new build (install, update) macOS checks every
# library the first time it loads: importing DuckDB, numpy and the rest then takes 10+ s instead of under one.
SPLASH = """<!doctype html><meta charset="utf-8"><style>
html,body{height:100%;margin:0;background:#0b0f13;color:#e6edf3;font:15px system-ui,sans-serif}
body{display:grid;place-content:center;gap:14px;text-align:center}
b{font-size:22px;letter-spacing:.04em}p{margin:0;color:#8b98a5;max-width:380px;line-height:1.5}
i{display:block;width:180px;height:3px;margin:6px auto 0;border-radius:3px;background:#222d38;overflow:hidden;position:relative}
i:after{content:"";position:absolute;inset:0;width:40%;background:#4aa3ff;animation:s 1.2s ease-in-out infinite}
@keyframes s{from{transform:translateX(-100%)}to{transform:translateX(250%)}}
</style><b>FM26 SCOUT</b><i></i><p>Starting…<br>The first launch after an install or an update takes a few more seconds while your computer checks the app.</p>"""
FAILED = SPLASH.split("<i>")[0] + "<p>FM26 Scout could not start. Close this window and open it again.</p>"


def is_scout(port) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as r:
            return json.load(r).get("app") == "fm26-scout"
    except Exception:
        return False


def _free(port) -> bool:
    with socket.socket() as s:
        # Same option as uvicorn's own bind, so a port left in TIME_WAIT by the app's last run counts as free.
        # Not on Windows, where it would let two sockets share a live port.
        if sys.platform != "win32":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def log_to_file():
    """A windowed build has no console (sys.stderr is None on Windows): keep what it prints in launcher.log, next to
    the data, so a window that falls back to the browser can be explained."""
    if sys.stderr is None or getattr(sys, "frozen", False):
        sys.stdout = sys.stderr = open(data_dir() / "launcher.log", "w", encoding="utf-8", buffering=1)


def unblock_dlls():
    """A zip downloaded from the web and extracted with Explorer tags every file as from the Internet (a
    Zone.Identifier stream). .NET then refuses to load pythonnet's and WebView2's assemblies, and the window falls
    back to the browser. Dropping the tag is what "Unblock" in the file's Properties does."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    from pathlib import Path
    for f in Path(sys._MEIPASS).rglob("*.dll"):
        try:
            os.remove(f"{f}:Zone.Identifier")
        except OSError:  # not tagged, or a read-only folder
            pass


def native_window(url, ready) -> bool:
    """Show the app in its own window (WKWebView on macOS, WebView2 on Windows) until it is closed: the splash
    first, the app once ready() says the server answers. False when no native window can be shown here: the caller
    falls back to the browser."""
    try:
        unblock_dlls()
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True  # CSV export
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # update link, GitHub
        w = webview.create_window("FM26 Scout", html=SPLASH, width=1440, height=900, min_size=(1000, 680),
                                  background_color="#0b0f13")
        # Not private: shortlist and saved searches live in localStorage, kept next to the data.
        def shown():
            print("native window", flush=True)  # the smoke test waits for this line
            w.load_url(url) if ready() else w.load_html(FAILED)
        webview.start(shown,
                      gui="edgechromium" if sys.platform == "win32" else None, private_mode=False,
                      storage_path=str(data_dir() / "webview"))
        return True
    except Exception:
        traceback.print_exc()
        return False


def main():
    log_to_file()
    if is_scout(PORT):  # already running: just show it
        if not native_window(f"http://127.0.0.1:{PORT}", lambda: True):
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    port = pick_port()
    url = f"http://127.0.0.1:{port}"
    box = {}

    def serve():  # the heavy imports happen here, behind the splash
        import uvicorn
        import app as scout
        box["scout"] = scout
        box["server"] = server = uvicorn.Server(uvicorn.Config(scout.app, host="127.0.0.1", port=port, log_config=None))
        server.run()

    def ready():
        if thread.ident is None:  # started once the splash shows: macOS loads one library at a time, so a server
            thread.start()        # already busy loading DuckDB (8 s on a first launch) would hold the window back
        while not is_scout(port):
            if not thread.is_alive():
                return False
            time.sleep(0.2)
        return True

    def watch():  # browser mode: quit once the tab is gone
        server = box["server"]
        while not server.should_exit:
            time.sleep(2)
            if box["scout"].idle_expired():
                server.should_exit = True

    # The window needs the main thread (AppKit, WinForms), so the server runs beside it.
    thread = threading.Thread(target=serve, daemon=True)
    if native_window(url, ready):  # returns when the window is closed
        if "server" not in box:  # closed during startup: nothing to stop cleanly
            return
        box["server"].should_exit = True
    else:
        if not ready():
            return
        threading.Thread(target=watch, daemon=True).start()
        webbrowser.open(url)
    thread.join()


if __name__ == "__main__":
    main()
