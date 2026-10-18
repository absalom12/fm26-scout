"""Launch the built app and check it serves the API and the bundled page, in its own window (not the browser).
Usage: python tests/smoke.py <executable>"""
import json, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path


def get(url):
    with urllib.request.urlopen(url, timeout=2) as r:
        return r.read()


def tag_as_downloaded(exe):
    """What Explorer does when it extracts the zip downloaded from the release page (the launcher must undo it)."""
    for f in Path(exe).parent.rglob("*.dll"):
        with open(f"{f}:Zone.Identifier", "w") as z:
            z.write("[ZoneTransfer]\nZoneId=3\n")


def main(exe):
    if sys.platform == "win32":
        tag_as_downloaded(exe)
    data = Path(tempfile.mkdtemp())
    proc = subprocess.Popen([exe], env={**os.environ, "FM26_DATA_DIR": str(data)})
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                sys.exit(f"app exited early with code {proc.returncode}")
            for port in range(8765, 8785):  # launcher picks the first free port from 8765
                try:
                    if json.loads(get(f"http://127.0.0.1:{port}/api/status")).get("app") == "fm26-scout":
                        assert b"<title>" in get(f"http://127.0.0.1:{port}/"), "bundled page missing"
                        print(f"ok on port {port}")
                        return check_window(data / "launcher.log")
                except Exception:
                    pass
            time.sleep(1)
        sys.exit("app did not answer within 90s")
    finally:
        proc.kill()


def check_window(log):
    """The launcher prints "native window" once its window is up; anything else means it fell back to the browser."""
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    if "native window" not in text:
        sys.exit("no native window, the app fell back to the browser. launcher.log:\n" + text)
    print("native window ok")


if __name__ == "__main__":
    main(sys.argv[1])
