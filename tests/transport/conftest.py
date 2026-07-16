# ABOUTME: Transport-tier fixtures: a function-scoped FakeServer speaking the
# ABOUTME: daemon wire protocol in-process.
from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.support import FakeServer


@pytest.fixture
def server() -> Iterator[FakeServer]:
    fake = FakeServer()
    yield fake
    fake.close()
