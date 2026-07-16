# ABOUTME: Core state machinery tests: SETOPT replay cache with v3 wholesale
# ABOUTME: replacement, connection_lost draining, replay encoding, tombstones.
import msgpack
import pytest

from sqe.errors import ConnectionLost, ProtocolError
from sqe.protocol import Connection
from tests.support import pack_reply


def ok_setopt(conn: Connection, host: str, port: int, options: dict) -> None:
    request_id, _ = conn.send_setopt(host, port, options)
    conn.feed(pack_reply([1 | 0x10, request_id, {}]))
    assert conn.next_response() is not None


def replayed_options(conn: Connection) -> dict[tuple[str, int], dict]:
    out = {}
    for _request_id, data in conn.replay_requests():
        req = msgpack.unpackb(data)
        assert req[0] == 1  # SETOPT
        out[(req[2], req[3])] = req[4]
    return out


def test_cache_merges_across_setopts() -> None:
    conn = Connection()
    ok_setopt(conn, "10.0.0.1", 161, {"community": "x"})
    ok_setopt(conn, "10.0.0.1", 161, {"timeout": 5000})
    ok_setopt(conn, "10.0.0.2", 161, {"version": 1})
    assert replayed_options(conn) == {
        ("10.0.0.1", 161): {"community": "x", "timeout": 5000},
        ("10.0.0.2", 161): {"version": 1},
    }


def test_failed_setopt_not_cached() -> None:
    conn = Connection()
    request_id, _ = conn.send_setopt("10.0.0.1", 161, {"community": "x"})
    conn.feed(pack_reply([1 | 0x20, request_id, "some error"]))
    assert conn.next_response() is not None
    assert conn.replay_requests() == []


def test_getopt_does_not_cache() -> None:
    conn = Connection()
    request_id, _ = conn.send_getopt("10.0.0.1", 161)
    conn.feed(pack_reply([2 | 0x10, request_id, {"community": "public", "version": 2}]))
    assert conn.next_response() is not None
    assert conn.replay_requests() == []


def test_v3_group_wholesale_replacement() -> None:
    conn = Connection()
    ok_setopt(
        conn,
        "10.0.0.1",
        161,
        {
            "version": 3,
            "engineid": "8000abcdef",
            "username": "alice",
            "authprotocol": "sha256",
            "authpassword": "secret1",
            "privprotocol": "aes",
            "privpassword": "secret2",
        },
    )
    # any v3 key present -> ALL cached v3 keys evicted first; non-v3 survive
    ok_setopt(conn, "10.0.0.1", 161, {"username": "bob"})
    assert replayed_options(conn) == {("10.0.0.1", 161): {"version": 3, "username": "bob"}}


def test_cached_engineid_is_the_hexencoded_form() -> None:
    conn = Connection()
    ok_setopt(conn, "10.0.0.1", 161, {"engineid": bytes.fromhex("8000abcdef")})
    assert replayed_options(conn) == {("10.0.0.1", 161): {"engineid": "8000abcdef"}}


def test_replay_requests_register_fresh_pendings() -> None:
    conn = Connection()
    ok_setopt(conn, "10.0.0.1", 161, {"community": "x"})
    replays = conn.replay_requests()
    assert len(replays) == 1
    request_id, _data = replays[0]
    conn.feed(pack_reply([1 | 0x10, request_id, {}]))
    response = conn.next_response()
    assert response is not None and response.request_id == request_id


def test_connection_lost_drains_pendings_preserves_cache() -> None:
    conn = Connection()
    ok_setopt(conn, "10.0.0.1", 161, {"community": "x"})
    id_a, _ = conn.send_get("10.0.0.1", 161, ["1.3"])
    id_b, _ = conn.send_getopt("10.0.0.1", 161)
    drained = conn.connection_lost()
    assert [r.request_id for r in drained] == [id_a, id_b]
    assert all(isinstance(r.error, ConnectionLost) for r in drained)
    assert conn.connection_lost() == []  # nothing pending anymore
    assert ("10.0.0.1", 161) in replayed_options(conn)  # cache survived


def test_connection_lost_resets_partial_frame() -> None:
    conn = Connection()
    request_id, _ = conn.send_get("10.0.0.1", 161, ["1.3"])
    wire = pack_reply([4 | 0x10, request_id, [["1.3", 1]]])
    conn.feed(wire[: len(wire) // 2])  # half a frame from the dying connection
    conn.connection_lost()
    fresh_id, _ = conn.send_get("10.0.0.1", 161, ["1.3"])
    conn.feed(pack_reply([4 | 0x10, fresh_id, [["1.3", 1]]]))
    response = conn.next_response()  # would blow up if the half-frame survived
    assert response is not None and response.request_id == fresh_id


def test_abandon_swallows_late_response_exactly_once() -> None:
    conn = Connection()
    request_id, _ = conn.send_get("10.0.0.1", 161, ["1.3"])
    conn.abandon(request_id)
    late = pack_reply([4 | 0x10, request_id, [["1.3", 1]]])
    conn.feed(late)
    assert conn.next_response() is None  # swallowed silently
    conn.feed(late)
    with pytest.raises(ProtocolError):  # tombstone was one-shot
        conn.next_response()


def test_abandon_unknown_id_is_noop() -> None:
    conn = Connection()
    conn.abandon(12345)
    request_id, _ = conn.send_get("10.0.0.1", 161, ["1.3"])
    conn.feed(pack_reply([4 | 0x10, request_id, [["1.3", 1]]]))
    assert conn.next_response() is not None
