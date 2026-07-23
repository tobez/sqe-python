# ABOUTME: SNMPv3 option round-trip through the real daemon: bytes engineid
# ABOUTME: hex-encoding, key localization lengths, and v3 group replacement.
import pytest

pytestmark = pytest.mark.integration

ENGINE_ID = bytes.fromhex("80004fb805636c6f75644dab22cc")
V3_OPTIONS = {
    "version": 3,
    "engineid": ENGINE_ID,  # bytes: exercises the bytes->hexstring convenience
    "username": "sqetest",
    "authprotocol": "sha256",
    "authpassword": "authpass123",
    "privprotocol": "aes",
    "privpassword": "privpass123",
}


def dehex(value: bytes) -> bytes:
    """Decode the daemon's hexstring form (possibly space-separated)."""
    return bytes.fromhex(value.decode().replace(" ", ""))


def test_v3_setopt_getopt_roundtrip(daemon, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port)
    opts = client.setopt("192.0.2.1", 161, V3_OPTIONS)
    assert opts["version"] == 3
    assert opts["username"] == b"sqetest"
    assert dehex(opts["engineid"]) == ENGINE_ID
    # engineid was supplied, so the passwords localize immediately; localized
    # keys have the digest length of the auth protocol (sha256 -> 32 bytes)
    assert len(dehex(opts["authkul"])) == 32
    assert len(dehex(opts["privkul"])) == 32
    # passwords are never returned
    assert not opts.get("authpassword")
    assert not opts.get("privpassword")

    got = client.getopt("192.0.2.1", 161)
    assert got["authkul"] == opts["authkul"]
    assert got["privkul"] == opts["privkul"]


def test_v3_group_wholesale_replacement_live(daemon, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port)
    client.setopt("192.0.2.2", 161, V3_OPTIONS)
    # any v3 option present replaces the WHOLE v3 group: priv settings vanish
    client.setopt(
        "192.0.2.2",
        161,
        {
            "engineid": ENGINE_ID,
            "username": "sqetest",
            "authprotocol": "sha256",
            "authpassword": "authpass123",
        },
    )
    got = client.getopt("192.0.2.2", 161)
    assert len(dehex(got["authkul"])) == 32
    assert not got.get("privkul")  # evicted, back to the empty default
