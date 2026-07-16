# ABOUTME: AsyncClient-specific tests: concurrent tasks on one loop, caller
# ABOUTME: cancellation abandoning the request, and async-with lifecycle.
import asyncio

import pytest

import sqe
from sqe import protocol
from tests.support import FakeServer, default_handler


def test_concurrent_tasks_get_their_own_answers(server: FakeServer) -> None:
    def handler(req: list):
        if req[0] == protocol.GET:
            return None  # test replies manually, reordered
        return default_handler(req)

    server.handler = handler

    async def main() -> None:
        async with sqe.AsyncClient("127.0.0.1", server.port) as client:
            task_a = asyncio.create_task(client.get("10.0.0.1", 161, ["1.3.1"]))
            task_b = asyncio.create_task(client.get("10.0.0.1", 161, ["1.3.2"]))
            await asyncio.to_thread(server.wait_request_count, 2)
            gets = [r for r in server.requests if r[0] == protocol.GET]
            for req in reversed(gets):  # answer in reverse order
                server.send([req[0] | 0x10, req[1], [[req[4][0], req[4][0].encode()]]])
            result_a = await task_a
            result_b = await task_b
        assert result_a == [sqe.VarBind("1.3.1", value=b"1.3.1")]
        assert result_b == [sqe.VarBind("1.3.2", value=b"1.3.2")]

    asyncio.run(main())


def test_cancelled_call_abandons_request(server: FakeServer) -> None:
    held: list[list] = []

    def handler(req: list):
        if req[0] == protocol.GET:
            held.append(req)
            return None
        return default_handler(req)

    server.handler = handler

    async def main() -> None:
        async with sqe.AsyncClient("127.0.0.1", server.port) as client:
            task = asyncio.create_task(client.get("10.0.0.1", 161, ["1.3.1"]))
            await asyncio.to_thread(server.wait_request_count, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            req = held[0]
            server.send([req[0] | 0x10, req[1], [["1.3.1", b"late"]]])
            info = await client.info()  # late reply swallowed; client healthy
            assert info["global"]["uptime"] == 1234

    asyncio.run(main())


def test_async_context_manager_closes(server: FakeServer) -> None:
    async def main() -> None:
        async with sqe.AsyncClient("127.0.0.1", server.port) as client:
            assert (await client.info())["global"]["uptime"] == 1234
        with pytest.raises(sqe.ConnectionLost):
            await client.info()

    asyncio.run(main())


def test_close_during_establish_does_not_leak_connection(
    server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open_connection = asyncio.open_connection
    started = asyncio.Event()
    gate = asyncio.Event()

    async def gated_open_connection(*args, **kwargs):
        started.set()
        await gate.wait()
        return await real_open_connection(*args, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", gated_open_connection)

    async def main() -> None:
        client = sqe.AsyncClient("127.0.0.1", server.port)
        connect_task = asyncio.create_task(client.connect())
        await started.wait()
        await client.close()
        gate.set()
        await connect_task
        assert client._writer is None
        assert client._reader_task is None

    asyncio.run(main())
