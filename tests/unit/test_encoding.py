# ABOUTME: Wire-byte vectors for the request encoders: every send_* method,
# ABOUTME: pinned against exact msgpack bytes and unpacked structure.
import msgpack

from sqe.protocol import Connection


def test_get_exact_wire_bytes() -> None:
    conn = Connection()
    request_id, data = conn.send_get("127.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"])
    assert request_id == 1
    assert data == bytes.fromhex(
        "950401a93132372e302e302e31cca191b1312e332e362e312e322e312e312e352e30"
    )


def test_setopt_exact_wire_bytes() -> None:
    conn = Connection()
    request_id, data = conn.send_setopt("10.0.0.1", 161, {"community": "x", "version": 2})
    assert request_id == 1
    assert data == bytes.fromhex(
        "950101a831302e302e302e31cca182a9636f6d6d756e697479a178a776657273696f6e02"
    )


def test_setopt_hexstring_convenience_exact_wire_bytes() -> None:
    conn = Connection()
    request_id, data = conn.send_setopt("10.0.0.1", 161, {"engineid": bytes.fromhex("8000abcdef")})
    assert request_id == 1
    assert data == bytes.fromhex(
        "950101a831302e302e302e31cca181a8656e67696e656964aa38303030616263646566"
    )


def test_all_encoders_structure_and_monotonic_ids() -> None:
    conn = Connection()
    cases = [
        (
            conn.send_setopt("10.0.0.1", 161, {"version": 2}),
            [1, 1, "10.0.0.1", 161, {"version": 2}],
        ),
        (conn.send_getopt("10.0.0.1", 161), [2, 2, "10.0.0.1", 161]),
        (conn.send_info(), [3, 3]),
        (
            conn.send_get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"]),
            [4, 4, "10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"]],
        ),
        (
            conn.send_gettable("10.0.0.1", 161, "1.3.6.1.2.1.2.2.1.2"),
            [5, 5, "10.0.0.1", 161, "1.3.6.1.2.1.2.2.1.2"],
        ),
        (
            conn.send_gettable("10.0.0.1", 161, "1.3.6.1.2.1.2.2.1.2", max_repetitions=20),
            [5, 6, "10.0.0.1", 161, "1.3.6.1.2.1.2.2.1.2", 20],
        ),
        (conn.send_dest_info("10.0.0.1", 161), [6, 7, "10.0.0.1", 161]),
    ]
    for (request_id, data), expected in cases:
        assert msgpack.unpackb(data) == expected
        assert request_id == expected[1]


def test_hexstring_options_bytes_encoded_str_passthrough() -> None:
    conn = Connection()
    options = {
        "engineid": bytes.fromhex("8000abcdef"),
        "authkul": "aa" * 20,
        "privkul": bytes.fromhex("bb" * 20),
        "community": b"binary-community",
    }
    _, data = conn.send_setopt("10.0.0.1", 161, options)
    sent = msgpack.unpackb(data)[4]
    assert sent["engineid"] == "8000abcdef"
    assert sent["authkul"] == "aa" * 20  # str passes through untouched
    assert sent["privkul"] == "bb" * 20
    assert sent["community"] == b"binary-community"  # only the 3 hexstring options convert
    assert isinstance(options["engineid"], bytes)  # caller's dict not mutated


def test_get_copies_oid_iterable() -> None:
    conn = Connection()
    _, data = conn.send_get("10.0.0.1", 161, iter(["1.3.6.1.2.1.1.5.0"]))
    assert msgpack.unpackb(data)[4] == ["1.3.6.1.2.1.1.5.0"]
