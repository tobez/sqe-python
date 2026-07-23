# ABOUTME: Live scenarios against a real snmp-query-engine daemon querying a
# ABOUTME: canned snmpsim agent; runs for both the sync and asyncio clients.
import pytest

import sqe

pytestmark = pytest.mark.integration


def test_get_exact_values(daemon, target, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port)
    varbinds = client.get(*target, ["1.3.6.1.2.1.1.5.0", "1.3.6.1.2.1.2.2.1.10.1"])
    assert varbinds == [
        sqe.VarBind("1.3.6.1.2.1.1.5.0", value=b"public.example.net"),
        sqe.VarBind("1.3.6.1.2.1.2.2.1.10.1", value=1000),
    ]


def test_setopt_getopt_roundtrip(daemon, target, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port)
    opts = client.setopt(*target, {"community": "public", "version": 2})
    assert opts["community"] == b"public"
    assert opts["version"] == 2
    got = client.getopt(*target)
    assert got["community"] == b"public"
    assert got["version"] == 2


def test_get_missing_oid_is_a_value_error_not_a_raise(daemon, target, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port)
    varbinds = client.get(*target, ["1.3.6.1.2.1.1.5.0", "1.3.66.1"])
    assert varbinds[0].ok
    assert not varbinds[1].ok
    assert varbinds[1].error in ("no-such-object", "no-such-instance", "missing")


def test_gettable_with_and_without_max_repetitions(daemon, target, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port)
    expected = [
        sqe.VarBind("1.3.6.1.2.1.2.2.1.2.1", value=b"eth0"),
        sqe.VarBind("1.3.6.1.2.1.2.2.1.2.2", value=b"eth1"),
        sqe.VarBind("1.3.6.1.2.1.2.2.1.2.3", value=b"eth2"),
    ]
    assert client.gettable(*target, "1.3.6.1.2.1.2.2.1.2") == expected
    assert client.gettable(*target, "1.3.6.1.2.1.2.2.1.2", 2) == expected


def test_gettable_of_leaf_oid_is_empty(daemon, target, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port)
    assert client.gettable(*target, "1.3.6.1.2.1.1.5.0") == []


def test_info_structure(daemon, target, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port)
    client.get(*target, ["1.3.6.1.2.1.1.5.0"])
    info = client.info()
    assert set(info) == {"connection", "global"}
    assert info["connection"]["get_requests"] >= 1
    assert isinstance(info["global"]["uptime"], int)
    assert isinstance(info["global"]["version"], bytes)


def test_dest_info_counts_octets(daemon, target, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port)
    client.get(*target, ["1.3.6.1.2.1.1.5.0"])
    dest = client.dest_info(*target)
    assert dest["octets_sent"] > 0
    assert dest["octets_received"] > 0


def test_bad_destination_raises_request_error(daemon, make_client) -> None:
    client = make_client("127.0.0.1", daemon.port)
    with pytest.raises(sqe.RequestError, match="[Bb]ad IP"):
        client.get("257.12.22.13", 161, ["1.3.6.1.2.1.1.5.0"])


def test_per_oid_timeout_from_mute_destination(daemon, make_client, mute_udp_port) -> None:
    client = make_client("127.0.0.1", daemon.port)
    client.setopt("127.0.0.1", mute_udp_port, {"timeout": 200, "retries": 1})
    varbinds = client.get("127.0.0.1", mute_udp_port, ["1.3.6.1.2.1.1.5.0"], timeout=10)
    assert varbinds == [sqe.VarBind("1.3.6.1.2.1.1.5.0", error="timeout")]
