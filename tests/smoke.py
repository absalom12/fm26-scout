"""Launch the built app and check it serves the API and the bundled page. Usage: python tests/smoke.py <executable>"""
import json, subprocess, sys, time, urllib.request


def get(url):
    with urllib.request.urlopen(url, timeout=2) as r:
        return r.read()


def main(exe):
    proc = subprocess.Popen([exe])
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
                        return
                except Exception:
                    pass
            time.sleep(1)
        sys.exit("app did not answer within 90s")
    finally:
        proc.kill()


if __name__ == "__main__":
    main(sys.argv[1])
