import json, socket, sys, threading, time, traceback, urllib.request, webbrowser

import uvicorn

import app as scout
from datadir import data_dir

PORT = 8765  # fixed: the page keeps shortlist/searches in localStorage, which is per origin (port included)


def is_scout(port) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as r:
            return json.load(r).get("app") == "fm26-scout"
    except Exception:
        return False


def _free(port) -> bool:
    with socket.socket() as s:
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


def native_window(url) -> bool:
    """Show the app in its own window (WKWebView on macOS, WebView2 on Windows) until it is closed.
    False when no native window can be shown here: the caller falls back to the browser."""
    try:
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True  # CSV export
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # update link, GitHub
        webview.create_window("FM26 Scout", url, width=1440, height=900, min_size=(1000, 680))
        # Not private: shortlist and saved searches live in localStorage, kept next to the data.
        webview.start(gui="edgechromium" if sys.platform == "win32" else None, private_mode=False,
                      storage_path=str(data_dir() / "webview"))
        return True
    except Exception:
        traceback.print_exc()
        return False


def main():
    if is_scout(PORT):  # already running: just show it
        if not native_window(f"http://127.0.0.1:{PORT}"):
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    port = pick_port()
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(scout.app, host="127.0.0.1", port=port, log_config=None))

    def watch():  # browser mode: quit once the tab is gone
        while not server.should_exit:
            time.sleep(2)
            if scout.idle_expired():
                server.should_exit = True

    # The window needs the main thread (AppKit, WinForms), so the server runs beside it.
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not is_scout(port):
        if not thread.is_alive():
            return
        time.sleep(0.2)
    if native_window(url):  # returns when the window is closed
        server.should_exit = True
    else:
        threading.Thread(target=watch, daemon=True).start()
        webbrowser.open(url)
    thread.join()


if __name__ == "__main__":
    main()
