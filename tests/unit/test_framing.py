# ABOUTME: Framing tests: responses fed byte-by-byte, split at every offset,
# ABOUTME: and coalesced into a single feed all decode identically.
from sqe.protocol import Connection, VarBind
from tests.support import pack_reply


def make_reply(request_id: int) -> bytes:
    return pack_reply([4 | 0x10, request_id, [["1.3.6.1.2.1.1.5.0", "host"]]])


EXPECTED = [VarBind("1.3.6.1.2.1.1.5.0", value=b"host")]


def test_byte_by_byte() -> None:
    conn = Connection()
    request_id, _ = conn.send_get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"])
    wire = make_reply(request_id)
    for byte in wire[:-1]:
        conn.feed(bytes([byte]))
        assert conn.next_response() is None
    conn.feed(wire[-1:])
    response = conn.next_response()
    assert response is not None and response.value == EXPECTED


def test_split_at_every_offset() -> None:
    for offset in range(1, len(make_reply(1))):
        conn = Connection()
        request_id, _ = conn.send_get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"])
        wire = make_reply(request_id)
        conn.feed(wire[:offset])
        conn.feed(wire[offset:])
        response = conn.next_response()
        assert response is not None and response.value == EXPECTED, f"split at {offset}"


def test_coalesced_frames() -> None:
    conn = Connection()
    id_a, _ = conn.send_get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"])
    id_b, _ = conn.send_get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"])
    conn.feed(make_reply(id_a) + make_reply(id_b))
    first = conn.next_response()
    second = conn.next_response()
    assert first is not None and first.request_id == id_a
    assert second is not None and second.request_id == id_b
    assert conn.next_response() is None
