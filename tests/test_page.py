import socket
import threading
import time

import pytest
import uvicorn

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUTSHEET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCOUTSHEET_SAVES_DIR", str(tmp_path / "saves"))
    (tmp_path / "saves").mkdir()
    import app
    monkeypatch.setattr(app, "STATUS", "empty")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(.05)
    yield f"http://127.0.0.1:{port}/"
    srv.should_exit = True
    t.join(5)


def test_page_runs_without_js_errors(server):
    # A top-level JS crash (e.g. a const used before its declaration) leaves only the header: the API tests can't see it.
    errors = []
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")  # installed Chrome (present on GitHub runners): no browser download
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))  # uncaught exceptions only: the 409 "no save" responses are expected
        page.goto(server)
        page.wait_for_timeout(1000)  # let startup code (status poll, first render) run; networkidle never comes: the page polls
        browser.close()
    assert errors == []
