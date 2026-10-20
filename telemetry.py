"""Opt-in usage stats. Asked once at first launch, changeable in Settings. Only daily counts per event are kept
(e.g. "export:full" 3 times on 2026-10-20), in a local file, and sent to scoutsheet.fm when online. Never sent: save
content, file paths, player, club or league names."""
import datetime, json, re, sys, threading, time, urllib.request, uuid

from datadir import data_dir
from updater import TLS

URL = "https://scoutsheet.fm/app-stats"
OS = {"darwin": "mac", "win32": "windows"}.get(sys.platform, "linux")
KEY = re.compile(r"^[a-z_]{1,32}(:[\w.-]{1,40})?$")
MAX_KEYS = 500  # per day: the queue stays small even after months offline
_lock = threading.Lock()


def _cfg():
    return data_dir() / "telemetry.json"


def _queue():
    return data_dir() / "telemetry-queue.json"


def _read(p):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def state():
    """{"asked": bool, "on": bool}"""
    c = _read(_cfg())
    return {"asked": "on" in c, "on": c.get("on") is True}


def set_consent(on):
    with _lock:
        c = _read(_cfg())
        if on:
            c = {**c, "on": True, "id": c.get("id") or str(uuid.uuid4())}
        else:  # the server deletes this install's counts at the next flush; nothing local is kept
            c = {"on": False, "forget": c.get("forget", []) + ([c["id"]] if c.get("id") else [])}
            _queue().unlink(missing_ok=True)
        _cfg().write_text(json.dumps(c))


def record(event, detail=None, n=1):
    key = f"{event}:{detail}" if detail else event
    if not KEY.match(key) or not 0 < n <= 86400:
        return
    with _lock:
        if _read(_cfg()).get("on") is not True:
            return
        q = _read(_queue())
        day = q.setdefault(datetime.date.today().isoformat(), {})
        if key in day or len(day) < MAX_KEYS:
            day[key] = day.get(key, 0) + int(n)
            _queue().write_text(json.dumps(q))


def _merge(into, q):
    for day, counts in q.items():
        d = into.setdefault(day, {})
        for k, n in counts.items():
            d[k] = d.get(k, 0) + n


def _post(body):
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15, context=TLS).close()


def flush(version):
    """Send what is queued. Offline or on any error, it stays queued for the next try."""
    with _lock:
        c = _read(_cfg())
        q = _read(_queue()) if c.get("on") is True else {}
        _queue().unlink(missing_ok=True)
    try:
        if c.get("forget"):
            _post({"forget": c["forget"]})
            with _lock:
                now = _read(_cfg())
                now["forget"] = [i for i in now.get("forget", []) if i not in c["forget"]]
                _cfg().write_text(json.dumps(now))
        if q:
            _post({"id": c["id"], "v": version, "os": OS, "days": q})
    except Exception:
        with _lock:  # put back, adding what was recorded meanwhile
            if q and _read(_cfg()).get("on") is True:
                _merge(q, _read(_queue()))
                _queue().write_text(json.dumps(q))


def run(version, every=600):
    while True:
        flush(version)
        time.sleep(every)
