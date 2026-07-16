# ABOUTME: Repo-wide fixtures: the make_client factory parametrized over the
# ABOUTME: sync and asyncio transports, so scenario tests run against both.
from __future__ import annotations

from typing import Any

import pytest

import sqe

TRANSPORTS = ["sync"]  # "aio" joins in the async-client PR


@pytest.fixture(params=TRANSPORTS)
def make_client(request: pytest.FixtureRequest) -> Any:
    created: list[Any] = []

    def factory(host: str, port: int, **kwargs: Any) -> Any:
        if request.param == "sync":
            client: Any = sqe.Client(host, port, **kwargs)
        else:
            from tests.support import AioDriver

            client = AioDriver(host, port, **kwargs)
        created.append(client)
        return client

    yield factory
    for client in created:
        client.close()
