# ABOUTME: Live reconnect scenarios: daemon restart mid-flight and between
# ABOUTME: requests, SETOPT replay after the bounce, reconnect=False breakage.
import time

import pytest

import sqe
from tests.support import call_in_thread

pytestmark = pytest.mark.integration


def test_restart_midflight_fails_inflight_with_connection_lost(
    daemon, make_client, mute_udp_port
) -> None:
    client = make_client("127.0.0.1", daemon.port, reconnect_initial_delay=0.05)
    client.setopt("127.0.0.1", mute_udp_port, {"timeout": 5000, "retries": 3})
    thread, result = call_in_thread(client.get, "127.0.0.1", mute_udp_port, ["1.3.6.1.2.1.1.5.0"])
    time.sleep(0.3)  # the GET is registered and on the wire
    daemon.restart()
    thread.join(10)
    assert isinstance(result["error"], sqe.ConnectionLost)


def test_restart_then_auto_reconnect_and_replay(daemon, target, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port, reconnect_initial_delay=0.05)
    client.setopt(*target, {"timeout": 5000})  # 5000 is a replay marker (default 2000)
    daemon.restart()
    varbinds = client.get(*target, ["1.3.6.1.2.1.1.5.0"], timeout=15)
    assert varbinds[0].value == b"public.example.net"
    got = client.getopt(*target)
    assert got["timeout"] == 5000  # fresh daemon, so this proves replay


def test_reconnect_false_breaks_until_explicit_connect(daemon, target, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port, reconnect=False)
    assert "global" in client.info()
    daemon.restart()
    guard = 0
    while True:  # wait until the client notices
        try:
            client.info(timeout=1)
        except sqe.ConnectionLost:
            break
        guard += 1
        assert guard < 100
    with pytest.raises(sqe.ConnectionLost):
        client.info()
    client.connect()
    assert "global" in client.info()
