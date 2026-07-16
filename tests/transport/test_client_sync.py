# ABOUTME: Sync-client-specific tests: context manager and thread concurrency
# ABOUTME: behaviors that have no driver-shared equivalent.
import pytest

import sqe
from sqe import protocol
from tests.support import FakeServer, call_in_thread, default_handler


def test_context_manager_closes(server: FakeServer) -> None:
    with sqe.Client("127.0.0.1", server.port) as client:
        assert client.info()["global"]["uptime"] == 1234
    with pytest.raises(sqe.ConnectionLost):
        client.info()


def test_concurrent_threads_get_their_own_answers(server: FakeServer) -> None:
    def handler(req: list):
        if req[0] == protocol.GET:
            return None  # test replies manually, reordered
        return default_handler(req)

    server.handler = handler
    with sqe.Client("127.0.0.1", server.port) as client:
        thread_a, result_a = call_in_thread(client.get, "10.0.0.1", 161, ["1.3.1"])
        thread_b, result_b = call_in_thread(client.get, "10.0.0.1", 161, ["1.3.2"])
        server.wait_request_count(2)
        gets = [r for r in server.requests if r[0] == protocol.GET]
        for req in reversed(gets):  # answer in reverse order
            server.send([req[0] | 0x10, req[1], [[req[4][0], req[4][0].encode()]]])
        thread_a.join(5)
        thread_b.join(5)
    assert result_a["value"] == [sqe.VarBind("1.3.1", value=b"1.3.1")]
    assert result_b["value"] == [sqe.VarBind("1.3.2", value=b"1.3.2")]
