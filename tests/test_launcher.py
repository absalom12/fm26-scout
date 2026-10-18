import socket
import sys

import pytest

from launcher import is_scout, pick_port


def test_pick_port_skips_taken_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); taken = s.getsockname()[1]
    try:
        assert pick_port(taken) != taken
    finally:
        s.close()


def test_is_scout_false_on_foreign_listener():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    try:
        assert not is_scout(s.getsockname()[1])
    finally:
        s.close()


def test_is_scout_false_when_nothing_listens():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    assert not is_scout(port)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows doesn't keep a closed port from binding")
def test_port_in_time_wait_counts_as_free():
    # Right after the app quits (update relaunch, quick reopen) its port sits in TIME_WAIT. Picking the next port
    # would change the page's origin and hide the shortlist and saved searches kept in localStorage.
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); srv.listen(); port = srv.getsockname()[1]
    c = socket.create_connection(("127.0.0.1", port)); a, _ = srv.accept()
    a.close(); srv.close(); c.close()  # server side closes first: its end goes to TIME_WAIT
    assert pick_port(port) == port
