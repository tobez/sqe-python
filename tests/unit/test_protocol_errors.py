# ABOUTME: Strictness tests: daemon error replies become RequestError responses;
# ABOUTME: every malformed reply shape raises ProtocolError.
import msgpack
import pytest

from sqe.errors import ProtocolError, RequestError
from sqe.protocol import Connection
from tests.support import pack_reply


def pending_get(conn: Connection) -> int:
    request_id, _ = conn.send_get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"])
    return request_id


def test_error_reply_becomes_request_error() -> None:
    conn = Connection()
    request_id = pending_get(conn)
    conn.feed(pack_reply([4 | 0x20, request_id, "bad IP address"]))
    response = conn.next_response()
    assert response is not None
    assert isinstance(response.error, RequestError)
    assert str(response.error) == "bad IP address"
    assert response.value is None


@pytest.mark.parametrize(
    "reply",
    [
        42,  # not an array
        [4 | 0x10],  # too short
        ["x", 1, []],  # non-int type
        [4 | 0x10, "x", []],  # non-int id
        [4 | 0x10, 1],  # success with no payload
        [4 | 0x10, 1, {"not": "an array"}],  # GET payload must be an array
        [4 | 0x10, 1, [["1.3", 1, 2]]],  # 3-element varbind row
        [4 | 0x10, 1, ["not-a-row"]],  # row not an array
        [4 | 0x10, 1, [[7, 1]]],  # non-string oid
        [4 | 0x10, 1, [["1.3", ["a", "b"]]]],  # error array with 2 elements
        [4 | 0x10, 1, [["1.3", [42]]]],  # non-string error
        [4 | 0x20, 1],  # error reply with no message
        [4 | 0x20, 1, 42],  # non-string error message
        [4 | 0x20, 1, "a", "b"],  # error reply too long
        [1 | 0x10, 1, {}],  # wrong type for a GET id
        [4, 1, []],  # neither |0x10 nor |0x20
    ],
)
def test_malformed_replies_raise_protocol_error(reply: object) -> None:
    conn = Connection()
    pending_get(conn)  # request id 1 is pending
    conn.feed(pack_reply(reply) if not isinstance(reply, int) else msgpack.packb(reply))
    with pytest.raises(ProtocolError):
        conn.next_response()


def test_unknown_request_id_raises() -> None:
    conn = Connection()
    pending_get(conn)
    conn.feed(pack_reply([4 | 0x10, 999, []]))
    with pytest.raises(ProtocolError, match="unknown request id"):
        conn.next_response()


def test_getopt_payload_must_be_map() -> None:
    conn = Connection()
    request_id, _ = conn.send_getopt("10.0.0.1", 161)
    conn.feed(pack_reply([2 | 0x10, request_id, ["not", "a", "map"]]))
    with pytest.raises(ProtocolError, match="not a map"):
        conn.next_response()


def test_malformed_msgpack_raises() -> None:
    conn = Connection()
    pending_get(conn)
    conn.feed(b"\xc1")  # 0xc1 is the one byte the msgpack spec never uses
    with pytest.raises(ProtocolError, match="malformed msgpack"):
        conn.next_response()
