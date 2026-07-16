# ABOUTME: Synchronous, thread-safe client for the snmp-query-engine daemon.
# ABOUTME: A background reader thread dispatches responses to blocked callers.

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from typing import Any, cast

from . import protocol
from .errors import ConnectionLost, ProtocolError

_Encoder = Callable[[protocol.Connection], "tuple[int, bytes]"]


def _close_quietly(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass


class _Waiter:
    __slots__ = ("event", "response")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.response: protocol.Response | None = None


class Client:
    """Synchronous client; safe for concurrent use from multiple threads.

    Construction does no I/O; the connection is established on first use
    (or by an explicit connect()).
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
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._conn = protocol.Connection()
        self._sock: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._waiters: dict[int, _Waiter] = {}
        self._connected = threading.Event()
        self._wake = threading.Event()  # interrupts reconnect backoff on close
        self._closed = False
        self._broken = False

    # -- lifecycle --

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        """Connect now; after a drop with reconnect=False this revives the client."""
        with self._lock:
            if self._closed:
                raise ConnectionLost("client is closed")
            self._broken = False
            if self._sock is None and self._reader is None:
                self._connect_locked()

    def close(self) -> None:
        """Close the connection; every in-flight request fails with ConnectionLost."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._wake.set()
            sock, self._sock = self._sock, None
            reader, self._reader = self._reader, None
            if sock is not None:
                _close_quietly(sock)
            for response in self._conn.connection_lost():
                self._dispatch_locked(response)
            self._connected.set()  # wake reconnect waiters; they re-check _closed
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=5)

    # -- requests --

    def setopt(
        self, host: str, port: int, options: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        opts = dict(options)
        return cast(
            "dict[str, Any]",
            self._request(lambda c: c.send_setopt(host, port, opts), timeout),
        )

    def getopt(self, host: str, port: int, *, timeout: float | None = None) -> dict[str, Any]:
        return cast("dict[str, Any]", self._request(lambda c: c.send_getopt(host, port), timeout))

    def info(self, *, timeout: float | None = None) -> dict[str, Any]:
        return cast("dict[str, Any]", self._request(lambda c: c.send_info(), timeout))

    def get(
        self, host: str, port: int, oids: list[str], *, timeout: float | None = None
    ) -> list[protocol.VarBind]:
        oid_list = list(oids)
        return cast(
            "list[protocol.VarBind]",
            self._request(lambda c: c.send_get(host, port, oid_list), timeout),
        )

    def gettable(
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
            self._request(lambda c: c.send_gettable(host, port, oid, max_repetitions), timeout),
        )

    def dest_info(self, host: str, port: int, *, timeout: float | None = None) -> dict[str, Any]:
        return cast(
            "dict[str, Any]", self._request(lambda c: c.send_dest_info(host, port), timeout)
        )

    # -- plumbing --

    def _request(self, encode: _Encoder, timeout: float | None) -> Any:
        deadline = None if timeout is None else time.monotonic() + timeout
        waiter = _Waiter()
        while True:
            self._await_link(deadline)
            with self._lock:
                if self._closed:
                    raise ConnectionLost("client is closed")
                if self._sock is None:
                    continue  # link dropped between checks; wait again
                request_id, data = encode(self._conn)
                self._waiters[request_id] = waiter
                sock = self._sock
            try:
                with self._send_lock:
                    sock.sendall(data)
            except OSError:
                pass  # the reader observes the drop and fails this waiter
            break
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        if not waiter.event.wait(remaining):
            with self._lock:
                self._conn.abandon(request_id)
                self._waiters.pop(request_id, None)
            raise TimeoutError(f"no response from daemon within {timeout} seconds")
        response = waiter.response
        assert response is not None
        if response.error is not None:
            raise response.error
        return response.value

    def _await_link(self, deadline: float | None) -> None:
        with self._lock:
            if self._closed:
                raise ConnectionLost("client is closed")
            if self._broken:
                raise ConnectionLost("connection lost; call connect() to retry")
            if self._sock is not None:
                return
            if self._reader is None:
                self._connect_locked()  # lazy first connect; errors propagate
                return
        # a reconnect is in progress; wait for the reader thread to restore it
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        if not self._connected.wait(remaining):
            raise TimeoutError("daemon connection was not restored in time")
        with self._lock:
            if self._closed:
                raise ConnectionLost("client is closed")
            if self._broken:
                raise ConnectionLost("connection lost; call connect() to retry")

    def _connect_locked(self) -> None:
        sock = socket.create_connection((self._host, self._port), timeout=self._connect_timeout)
        sock.settimeout(None)
        self._attach_locked(sock)
        self._reader = threading.Thread(target=self._reader_main, args=(sock,), daemon=True)
        self._reader.start()

    def _attach_locked(self, sock: socket.socket) -> None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        # Safe under _lock: the reader for this socket starts only after we return,
        # so no concurrent reader can starve waiting for the lock while this send happens.
        self._send_payloads(sock, self._conn.replay_requests())
        self._connected.set()

    def _send_payloads(self, sock: socket.socket, payloads: list[tuple[int, bytes]]) -> None:
        """Send pre-encoded payloads, serialized against concurrent senders."""
        with self._send_lock:
            for _request_id, data in payloads:
                try:
                    sock.sendall(data)
                except OSError:
                    break  # the reader observes the drop

    def _reader_main(self, sock: socket.socket) -> None:
        while True:
            self._read_until_drop(sock)
            next_sock = self._handle_drop(sock)
            if next_sock is None:
                return
            sock = next_sock

    def _read_until_drop(self, sock: socket.socket) -> None:
        while True:
            try:
                data = sock.recv(65536)
            except OSError:
                return
            if not data:
                return
            with self._lock:
                try:
                    self._conn.feed(data)
                    while (response := self._conn.next_response()) is not None:
                        self._dispatch_locked(response)
                except ProtocolError:
                    return  # connection-fatal; treated exactly like a drop

    def _handle_drop(self, sock: socket.socket) -> socket.socket | None:
        with self._lock:
            if self._closed or sock is not self._sock:
                return None
            self._connected.clear()
            self._sock = None
            _close_quietly(sock)
            for response in self._conn.connection_lost():
                self._dispatch_locked(response)
            if not self._reconnect:
                self._broken = True
                self._reader = None
                return None
        return self._reconnect_link()

    def _reconnect_link(self) -> socket.socket | None:
        delay = self._reconnect_initial_delay
        while True:
            with self._lock:
                if self._closed:
                    return None
            try:
                sock = socket.create_connection(
                    (self._host, self._port), timeout=self._connect_timeout
                )
                sock.settimeout(None)
            except OSError:
                if self._wake.wait(delay):
                    return None  # close() interrupted the backoff
                delay = min(delay * 2, self._reconnect_max_delay)
                continue
            with self._lock:
                if self._closed:
                    _close_quietly(sock)
                    return None
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                payloads = self._conn.replay_requests()
            # send outside self._lock: a full send buffer must not block the
            # reader (or any other caller) out of the lock while replaying
            self._send_payloads(sock, payloads)
            with self._lock:
                if self._closed:
                    _close_quietly(sock)
                    return None
                self._sock = sock
                self._connected.set()
            return sock

    def _dispatch_locked(self, response: protocol.Response) -> None:
        waiter = self._waiters.pop(response.request_id, None)
        if waiter is not None:
            waiter.response = response
            waiter.event.set()
        # responses with no waiter (replayed SETOPTs, abandoned calls) drop here
