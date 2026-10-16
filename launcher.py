import json, socket, threading, time, urllib.request, webbrowser

import uvicorn

import app as scout

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


def main():
    if is_scout(PORT):  # already running: just show it
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    port = pick_port()
    server = uvicorn.Server(uvicorn.Config(scout.app, host="127.0.0.1", port=port, log_config=None))

    def watch():  # quit once the tab is gone
        while not server.should_exit:
            time.sleep(2)
            if scout.idle_expired():
                server.should_exit = True

    def open_when_up():
        while not is_scout(port):
            time.sleep(0.3)
        webbrowser.open(f"http://127.0.0.1:{port}")

    threading.Thread(target=watch, daemon=True).start()
    threading.Thread(target=open_when_up, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
