# ABOUTME: Sync-client-specific tests: context manager and thread concurrency
# ABOUTME: behaviors that have no driver-shared equivalent.
import pytest

import sqe
from tests.support import FakeServer


def test_context_manager_closes(server: FakeServer) -> None:
    with sqe.Client("127.0.0.1", server.port) as client:
        assert client.info()["global"]["uptime"] == 1234
    with pytest.raises(sqe.ConnectionLost):
        client.info()
