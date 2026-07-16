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
