# ABOUTME: Transport scenarios shared by the sync and asyncio clients via the
# ABOUTME: parametrized make_client fixture, against the in-process FakeServer.
import socket

import pytest

import sqe
from sqe import protocol


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
