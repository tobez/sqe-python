# ABOUTME: Transport scenarios shared by the sync and asyncio clients via the
# ABOUTME: parametrized make_client fixture, against the in-process FakeServer.
import socket
import time

import pytest

import sqe
from sqe import protocol
from tests.support import call_in_thread, pack_reply


def test_get_returns_varbinds(server, make_client) -> None:
    client = make_client("127.0.0.1", server.port)
    varbinds = client.get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0", "1.3.9.9"])
    assert varbinds == [
        sqe.VarBind("1.3.6.1.2.1.1.5.0", value=b"fake.example.net"),
        sqe.VarBind("1.3.9.9", error="no-such-object"),
    ]
    assert varbinds[0].ok and not varbinds[1].ok


def test_setopt_and_getopt_maps(server, make_client) -> None:
    client = make_client("127.0.0.1", server.port)
    opts = client.setopt("10.0.0.1", 161, {"community": "x", "version": 2})
    assert opts["community"] == b"x"  # string values arrive as bytes
    assert opts["version"] == 2
    assert client.getopt("10.0.0.1", 161) == {"version": 2, "community": b"public"}


def test_gettable_with_and_without_max_repetitions(server, make_client) -> None:
    client = make_client("127.0.0.1", server.port)
    expected = [
        sqe.VarBind("1.3.6.1.2.1.2.2.1.2.1", value=b"eth0"),
        sqe.VarBind("1.3.6.1.2.1.2.2.1.2.2", value=b"eth1"),
    ]
    assert client.gettable("10.0.0.1", 161, "1.3.6.1.2.1.2.2.1.2") == expected
    assert client.gettable("10.0.0.1", 161, "1.3.6.1.2.1.2.2.1.2", 2) == expected
    with_mr = [r for r in server.requests if r[0] == protocol.GETTABLE and len(r) == 6]
    assert len(with_mr) == 1 and with_mr[0][5] == 2


def test_info_and_dest_info(server, make_client) -> None:
    client = make_client("127.0.0.1", server.port)
    assert client.info() == {"connection": {"client_requests": 1}, "global": {"uptime": 1234}}
    assert client.dest_info("10.0.0.1", 161) == {"octets_received": 10, "octets_sent": 20}


def test_request_error_raises(server, make_client) -> None:
    server.handler = lambda req: [req[0] | 0x20, req[1], "bad IP address"]
    client = make_client("127.0.0.1", server.port)
    with pytest.raises(sqe.RequestError, match="bad IP address"):
        client.get("257.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"])
    # the client survives a RequestError
    server.handler = lambda req: [req[0] | 0x10, req[1], {"connection": {}, "global": {}}]
    assert client.info() == {"connection": {}, "global": {}}


def test_lazy_connect_failure_propagates(make_client) -> None:
    probe = socket.create_server(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()  # nothing listens here now
    client = make_client("127.0.0.1", dead_port)  # constructing never raises
    with pytest.raises(OSError):
        client.info()


def test_close_is_idempotent_and_final(server, make_client) -> None:
    client = make_client("127.0.0.1", server.port)
    assert client.info()["global"]["uptime"] == 1234
    client.close()
    client.close()
    with pytest.raises(sqe.ConnectionLost):
        client.info()


def test_drop_without_reconnect_breaks_until_connect(server, make_client) -> None:
    client = make_client("127.0.0.1", server.port, reconnect=False)
    client.setopt("10.0.0.1", 161, {"community": "s"})
    seen = len(server.requests)
    server.drop_client()
    deadline_guard = 0
    while True:  # wait until the client notices the drop
        try:
            client.info(timeout=1)
        except sqe.ConnectionLost:
            break
        deadline_guard += 1
        assert deadline_guard < 100
    with pytest.raises(sqe.ConnectionLost):
        client.info()  # still broken
    client.connect()  # explicit revival
    server.wait_request_count(seen + 1)  # first thing on the new link: SETOPT replay
    assert server.requests[seen][0] == protocol.SETOPT
    assert server.requests[seen][2:] == ["10.0.0.1", 161, {"community": "s"}]
    assert client.info()["global"]["uptime"] == 1234


def test_per_call_timeout_and_late_reply_swallowed(server, make_client) -> None:
    held: list[list] = []

    def handler(req: list):
        if req[0] == protocol.GET:
            held.append(req)
            return None  # never answer GETs (for now)
        from tests.support import default_handler

        return default_handler(req)

    server.handler = handler
    client = make_client("127.0.0.1", server.port)
    with pytest.raises(TimeoutError):
        client.get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"], timeout=0.2)
    # push the late reply for the abandoned request: must be swallowed
    req = held[0]
    server.send([req[0] | 0x10, req[1], [[req[4][0], b"late"]]])
    # a healthy follow-up call proves no ProtocolError/drop happened
    assert client.info()["global"]["uptime"] == 1234


def test_drop_fails_inflight_with_connection_lost(server, make_client) -> None:
    server.handler = lambda req: None  # hold everything
    client = make_client("127.0.0.1", server.port, reconnect_initial_delay=0.05)
    thread, result = call_in_thread(client.get, "10.0.0.1", 161, ["1.3.1"])
    server.wait_request_count(1)
    server.drop_client()
    thread.join(5)
    assert isinstance(result["error"], sqe.ConnectionLost)


def test_auto_reconnect_replays_setopts_before_traffic(server, make_client) -> None:
    client = make_client("127.0.0.1", server.port, reconnect_initial_delay=0.05)
    client.setopt("10.0.0.1", 161, {"community": "priv", "version": 2})
    seen = len(server.requests)
    server.drop_client()
    server.wait_request_count(seen + 1)  # the replay arrives on the new link
    replay = server.requests[seen]
    assert replay[0] == protocol.SETOPT
    assert replay[2:] == ["10.0.0.1", 161, {"community": "priv", "version": 2}]
    assert client.info()["global"]["uptime"] == 1234
    assert server.connections == 2


def test_calls_block_while_daemon_down_then_complete(server, make_client) -> None:
    client = make_client("127.0.0.1", server.port, reconnect_initial_delay=0.05)
    assert client.info()["global"]["uptime"] == 1234
    server.stop()
    time.sleep(0.2)  # let the reader observe the drop
    thread, result = call_in_thread(client.info)
    time.sleep(0.3)
    assert thread.is_alive()  # blocked, not failed
    server.start()
    thread.join(10)
    assert result["value"]["global"]["uptime"] == 1234


def test_blocked_call_times_out_while_daemon_down(server, make_client) -> None:
    client = make_client("127.0.0.1", server.port, reconnect_initial_delay=0.05)
    client.info()
    server.stop()
    time.sleep(0.2)
    with pytest.raises(TimeoutError):
        client.info(timeout=0.3)
    server.start()  # leave the room tidy for teardown


def test_garbage_from_daemon_is_connection_fatal_then_recovers(server, make_client) -> None:
    client = make_client("127.0.0.1", server.port, reconnect_initial_delay=0.05)
    assert client.info()["global"]["uptime"] == 1234
    server.send(b"\xc1")  # invalid msgpack: ProtocolError inside
    server.wait_connections(2)  # client treated it as a drop + reconnected
    assert client.info()["global"]["uptime"] == 1234


def test_reply_before_garbage_in_one_batch_still_dispatches_then_recovers(
    server, make_client
) -> None:
    server.handler = lambda req: None  # hold the GET so we can answer it ourselves
    client = make_client("127.0.0.1", server.port, reconnect_initial_delay=0.05)
    thread, result = call_in_thread(client.get, "10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"])
    server.wait_request_count(1)
    req = server.requests[0]
    reply = [req[0] | 0x10, req[1], [[req[4][0], b"fake.example.net"]]]
    # one write: a decodable reply immediately followed by a malformed frame
    server.send(pack_reply(reply) + b"\xc1")
    thread.join(5)
    # the in-flight caller got its value, not ConnectionLost from the trailing garbage
    assert result["value"] == [sqe.VarBind("1.3.6.1.2.1.1.5.0", value=b"fake.example.net")]
    server.wait_connections(2)  # the malformed frame still killed the connection
    from tests.support import default_handler

    server.handler = default_handler
    assert client.info()["global"]["uptime"] == 1234  # recovers on the new link
