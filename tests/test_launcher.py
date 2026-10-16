import socket
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
