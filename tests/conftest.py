# ABOUTME: Repo-wide fixtures: the make_client factory parametrized over the
# ABOUTME: sync and asyncio transports, so scenario tests run against both.
from __future__ import annotations

from typing import Any

import pytest

import sqe

TRANSPORTS = ["sync", "aio"]


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
    first_error: Exception | None = None
    for client in created:
        try:
            client.close()
        except Exception as exc:  # noqa: BLE001 — teardown must reach every client
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error
