# ABOUTME: Response decoding vectors: per-type payload decoding, bytes->str
# ABOUTME: normalization of keys/OIDs/errors, and request-id matching.
from sqe.protocol import Connection, VarBind
from tests.support import pack_reply


def roundtrip(conn: Connection, request_id: int, reply: list) -> object:
    conn.feed(pack_reply(reply))
    response = conn.next_response()
    assert response is not None
    assert response.request_id == request_id
    assert response.error is None
    assert conn.next_response() is None
    return response.value


def test_get_decodes_varbinds() -> None:
    conn = Connection()
    request_id, _ = conn.send_get(
        "10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0", "1.3.6.1.2.1.25.1.1.0", "1.3.66"]
    )
    value = roundtrip(
        conn,
        request_id,
        [
            4 | 0x10,
            request_id,
            [
                ["1.3.6.1.2.1.1.5.0", "my.host.name"],
                ["1.3.6.1.2.1.25.1.1.0", 215485727],
                ["1.3.66", ["no-such-object"]],
            ],
        ],
    )
    assert value == [
        VarBind("1.3.6.1.2.1.1.5.0", value=b"my.host.name"),
        VarBind("1.3.6.1.2.1.25.1.1.0", value=215485727),
        VarBind("1.3.66", error="no-such-object"),
    ]


def test_get_every_error_shape() -> None:
    errors = [
        "no-such-object",
        "no-such-instance",
        "end-of-mib",
        "timeout",
        "ignored",
        "missing",
        "decode-error",
        "unsupported type 0x2a",
        "engine-id-mismatch: 8000abcdef",
        "kul-calculation-error",
    ]
    conn = Connection()
    request_id, _ = conn.send_get("10.0.0.1", 161, ["1.3"] * len(errors))
    rows = [["1.3", [err]] for err in errors]
    value = roundtrip(conn, request_id, [4 | 0x10, request_id, rows])
    assert [vb.error for vb in value] == errors
    assert not any(vb.ok for vb in value)


def test_gettable_decodes_rows_and_empty_table() -> None:
    conn = Connection()
    request_id, _ = conn.send_gettable("10.0.0.1", 161, "1.3.6.1.2.1.2.2.1.2")
    value = roundtrip(
        conn,
        request_id,
        [
            5 | 0x10,
            request_id,
            [["1.3.6.1.2.1.2.2.1.2.1", "eth0"], ["1.3.6.1.2.1.2.2.1.2.2", ["non-increasing"]]],
        ],
    )
    assert value == [
        VarBind("1.3.6.1.2.1.2.2.1.2.1", value=b"eth0"),
        VarBind("1.3.6.1.2.1.2.2.1.2.2", error="non-increasing"),
    ]

    request_id, _ = conn.send_gettable("10.0.0.1", 161, "1.3.6.1.2.1.1.5.0")
    assert roundtrip(conn, request_id, [5 | 0x10, request_id, []]) == []


def test_setopt_map_keys_str_values_untouched() -> None:
    conn = Connection()
    request_id, _ = conn.send_setopt("10.0.0.1", 161, {"community": "x"})
    value = roundtrip(conn, request_id, [1 | 0x10, request_id, {"community": "x", "version": 2}])
    assert value == {"community": b"x", "version": 2}


def test_info_nested_maps_normalized() -> None:
    conn = Connection()
    request_id, _ = conn.send_info()
    value = roundtrip(
        conn,
        request_id,
        [
            3 | 0x10,
            request_id,
            {"connection": {"client_requests": 1}, "global": {"uptime": 12, "version": "2.2.0"}},
        ],
    )
    assert value == {
        "connection": {"client_requests": 1},
        "global": {"uptime": 12, "version": b"2.2.0"},
    }


def test_dest_info_decodes_map() -> None:
    conn = Connection()
    request_id, _ = conn.send_dest_info("10.0.0.1", 161)
    value = roundtrip(
        conn, request_id, [6 | 0x10, request_id, {"octets_received": 10, "octets_sent": 20}]
    )
    assert value == {"octets_received": 10, "octets_sent": 20}


def test_out_of_order_responses_match_by_id() -> None:
    conn = Connection()
    id_a, _ = conn.send_get("10.0.0.1", 161, ["1.3.1"])
    id_b, _ = conn.send_get("10.0.0.1", 161, ["1.3.2"])
    conn.feed(pack_reply([4 | 0x10, id_b, [["1.3.2", 2]]]))
    conn.feed(pack_reply([4 | 0x10, id_a, [["1.3.1", 1]]]))
    first = conn.next_response()
    second = conn.next_response()
    assert first is not None and first.request_id == id_b
    assert first.value == [VarBind("1.3.2", value=2)]
    assert second is not None and second.request_id == id_a
    assert second.value == [VarBind("1.3.1", value=1)]
    assert conn.next_response() is None
