# ABOUTME: asyncio client for the snmp-query-engine daemon: a reader task
# ABOUTME: dispatches responses to per-request futures; auto-reconnect built in.

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any, cast

from . import protocol
from .errors import ConnectionLost, ProtocolError

_Encoder = Callable[[protocol.Connection], "tuple[int, bytes]"]


class AsyncClient:
    """asyncio client; safe for concurrent use from many tasks of one loop.

    Construction does no I/O; the connection is established on first use
    (or by an explicit await connect()).
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7667,
        *,
        reconnect: bool = True,
        reconnect_initial_delay: float = 0.1,
        reconnect_max_delay: float = 5.0,
        connect_timeout: float = 5.0,
    ) -> None:
        self._host = host
        self._port = port
        self._reconnect = reconnect
        self._reconnect_initial_delay = reconnect_initial_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._connect_timeout = connect_timeout
        self._conn = protocol.Connection()
        self._stream: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._futures: dict[int, asyncio.Future[protocol.Response]] = {}
        self._connected = asyncio.Event()
        self._connect_lock = asyncio.Lock()
        self._closed = False
        self._broken = False

    # -- lifecycle --

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        """Connect now; after a drop with reconnect=False this revives the client."""
        async with self._connect_lock:
            if self._closed:
                raise ConnectionLost("client is closed")
            self._broken = False
            if self._writer is None and self._reader_task is None:
                await self._establish()

    async def close(self) -> None:
        """Close the connection; every in-flight request fails with ConnectionLost."""
        if self._closed:
            return
        self._closed = True
        task, self._reader_task = self._reader_task, None
        writer, self._writer = self._writer, None
        for response in self._conn.connection_lost():
            self._dispatch(response)
        self._connected.set()  # wake reconnect waiters; they re-check _closed
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    # -- requests --

    async def setopt(
        self, host: str, port: int, options: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        opts = dict(options)
        return cast(
            "dict[str, Any]",
            await self._request(lambda c: c.send_setopt(host, port, opts), timeout),
        )

    async def getopt(
        self, host: str, port: int, *, timeout: float | None = None
    ) -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            await self._request(lambda c: c.send_getopt(host, port), timeout),
        )

    async def info(self, *, timeout: float | None = None) -> dict[str, Any]:
        return cast("dict[str, Any]", await self._request(lambda c: c.send_info(), timeout))

    async def get(
        self, host: str, port: int, oids: list[str], *, timeout: float | None = None
    ) -> list[protocol.VarBind]:
        oid_list = list(oids)
        return cast(
            "list[protocol.VarBind]",
            await self._request(lambda c: c.send_get(host, port, oid_list), timeout),
        )

    async def gettable(
        self,
        host: str,
        port: int,
        oid: str,
        max_repetitions: int | None = None,
        *,
        timeout: float | None = None,
    ) -> list[protocol.VarBind]:
        return cast(
            "list[protocol.VarBind]",
            await self._request(
                lambda c: c.send_gettable(host, port, oid, max_repetitions), timeout
            ),
        )

    async def dest_info(
        self, host: str, port: int, *, timeout: float | None = None
    ) -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            await self._request(lambda c: c.send_dest_info(host, port), timeout),
        )

    # -- plumbing --

    async def _request(self, encode: _Encoder, timeout: float | None) -> Any:
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while True:
            await self._await_link(deadline)
            if self._closed:
                raise ConnectionLost("client is closed")
            if self._writer is None:
                continue  # link dropped between checks; wait again
            request_id, data = encode(self._conn)
            future: asyncio.Future[protocol.Response] = loop.create_future()
            self._futures[request_id] = future
            self._writer.write(data)
            break
        try:
            remaining = None if deadline is None else max(0.0, deadline - loop.time())
            response = await asyncio.wait_for(future, remaining)
        except asyncio.TimeoutError:
            self._abandon(request_id)
            raise TimeoutError(f"no response from daemon within {timeout} seconds") from None
        except asyncio.CancelledError:
            self._abandon(request_id)
            raise
        if response.error is not None:
            raise response.error
        return response.value

    def _abandon(self, request_id: int) -> None:
        self._conn.abandon(request_id)
        self._futures.pop(request_id, None)

    async def _await_link(self, deadline: float | None) -> None:
        if self._closed:
            raise ConnectionLost("client is closed")
        if self._broken:
            raise ConnectionLost("connection lost; call connect() to retry")
        if self._writer is not None:
            return
        if self._reader_task is None:
            async with self._connect_lock:
                if self._closed:
                    raise ConnectionLost("client is closed")
                if self._writer is None and self._reader_task is None:
                    await self._establish()  # lazy first connect; errors propagate
            return
        loop = asyncio.get_running_loop()
        remaining = None if deadline is None else max(0.0, deadline - loop.time())
        try:
            await asyncio.wait_for(self._connected.wait(), remaining)
        except asyncio.TimeoutError:
            raise TimeoutError("daemon connection was not restored in time") from None
        if self._closed:
            raise ConnectionLost("client is closed")
        if self._broken:
            raise ConnectionLost("connection lost; call connect() to retry")

    async def _establish(self) -> None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), self._connect_timeout
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"connection to {self._host}:{self._port} timed out after "
                f"{self._connect_timeout} seconds"
            ) from None
        self._stream = reader
        self._attach(writer)
        self._reader_task = asyncio.get_running_loop().create_task(self._reader_main())

    def _attach(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        for _request_id, data in self._conn.replay_requests():
            writer.write(data)
        self._connected.set()

    async def _reader_main(self) -> None:
        while True:
            await self._read_until_drop()
            if not await self._handle_drop():
                return

    async def _read_until_drop(self) -> None:
        stream = self._stream
        assert stream is not None
        while True:
            try:
                data = await stream.read(65536)
            except OSError:
                return
            if not data:
                return
            try:
                self._conn.feed(data)
                while (response := self._conn.next_response()) is not None:
                    self._dispatch(response)
            except ProtocolError:
                return  # connection-fatal; treated exactly like a drop

    async def _handle_drop(self) -> bool:
        if self._closed:
            return False
        self._connected.clear()
        old_writer, self._writer = self._writer, None
        if old_writer is not None:
            old_writer.close()
            with contextlib.suppress(OSError):
                await old_writer.wait_closed()
        for response in self._conn.connection_lost():
            self._dispatch(response)
        if not self._reconnect:
            self._broken = True
            self._reader_task = None
            return False
        delay = self._reconnect_initial_delay
        while True:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port), self._connect_timeout
                )
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max_delay)
                if self._closed:
                    return False
                continue
            if self._closed:
                writer.close()
                return False
            self._stream = reader
            self._attach(writer)
            return True

    def _dispatch(self, response: protocol.Response) -> None:
        future = self._futures.pop(response.request_id, None)
        if future is not None and not future.done():
            future.set_result(response)
        # responses with no future (replayed SETOPTs, abandoned calls) drop here
