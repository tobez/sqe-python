# ABOUTME: Shared test helpers: daemon-style reply packing (all strings as
# ABOUTME: msgpack bin), an in-process fake daemon, and thread-call helpers.
from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import msgpack

from sqe import protocol


def binify(obj: Any) -> Any:
    """Encode all strings as bytes, mirroring the daemon's bin-everything replies."""
    if isinstance(obj, str):
        return obj.encode()
    if isinstance(obj, dict):
        return {binify(k): binify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [binify(v) for v in obj]
    return obj


def pack_reply(obj: Any) -> bytes:
    """Pack a reply structure the way the daemon would put it on the wire."""
    packed: bytes = msgpack.packb(binify(obj))
    return packed


Handler = Callable[[list], Any]

CANNED_OIDS: dict[str, Any] = {
    "1.3.6.1.2.1.1.5.0": b"fake.example.net",
    "1.3.6.1.2.1.2.2.1.10.1": 1000,
}

CANNED_TABLE: dict[str, list] = {
    "1.3.6.1.2.1.2.2.1.2": [
        ["1.3.6.1.2.1.2.2.1.2.1", b"eth0"],
        ["1.3.6.1.2.1.2.2.1.2.2", b"eth1"],
    ],
}

CANNED_INFO = {"connection": {"client_requests": 1}, "global": {"uptime": 1234}}


def default_handler(req: list) -> Any:
    """Canned daemon behavior, close enough for transport tests."""
    rtype, request_id = req[0], req[1]
    if rtype == protocol.SETOPT:
        return [rtype | 0x10, request_id, {"version": 2, "community": "public", **req[4]}]
    if rtype == protocol.GETOPT:
        return [rtype | 0x10, request_id, {"version": 2, "community": "public"}]
    if rtype == protocol.INFO:
        return [rtype | 0x10, request_id, CANNED_INFO]
    if rtype == protocol.GET:
        rows = [
            [oid, CANNED_OIDS[oid]] if oid in CANNED_OIDS else [oid, ["no-such-object"]]
            for oid in req[4]
        ]
        return [rtype | 0x10, request_id, rows]
    if rtype == protocol.GETTABLE:
        return [rtype | 0x10, request_id, CANNED_TABLE.get(req[4], [])]
    if rtype == protocol.DEST_INFO:
        return [rtype | 0x10, request_id, {"octets_received": 10, "octets_sent": 20}]
    return [rtype | 0x20, request_id, "unknown request type"]


class FakeServer:
    """In-process TCP server speaking the daemon's wire protocol.

    One client connection at a time. The handler maps each decoded request
    to a reply: a list (packed with daemon-style bin strings), raw bytes
    (sent verbatim), or None (no reply). Supports dropping the client and
    stop/start on the same port, for reconnect tests.
    """

    def __init__(self, handler: Handler | None = None) -> None:
        self.handler: Handler = handler or default_handler
        self.requests: list[list] = []
        self.connections = 0
        self._lock = threading.Lock()
        self._client: socket.socket | None = None
        self._closed = False
        self._listener: socket.socket | None = socket.create_server(("127.0.0.1", 0))
        self.port: int = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        listener = self._listener
        assert listener is not None
        listener.settimeout(0.1)
        while not self._closed and self._listener is listener:
            try:
                client, _addr = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.connections += 1
            with self._lock:
                self._client = client
            self._talk(client)

    def _talk(self, client: socket.socket) -> None:
        unpacker = msgpack.Unpacker()
        client.settimeout(0.1)
        while not self._closed and self._client is client:
            try:
                data = client.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                break
            if not data:
                break
            unpacker.feed(data)
            for req in unpacker:
                self.requests.append(req)
                reply = self.handler(req)
                if reply is None:
                    continue
                out = reply if isinstance(reply, bytes) else pack_reply(reply)
                try:
                    client.sendall(out)
                except OSError:
                    break
        with self._lock:
            if self._client is client:
                self._client = None
        client.close()

    def send(self, reply: Any) -> None:
        """Push a reply (list or raw bytes) to the currently connected client."""
        with self._lock:
            client = self._client
        assert client is not None, "no client connected"
        client.sendall(reply if isinstance(reply, bytes) else pack_reply(reply))

    def wait_request_count(self, n: int, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while len(self.requests) < n:
            if time.monotonic() > deadline:
                raise AssertionError(f"saw {len(self.requests)} requests, wanted {n}")
            time.sleep(0.01)

    def wait_connections(self, n: int, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while self.connections < n:
            if time.monotonic() > deadline:
                raise AssertionError(f"saw {self.connections} connections, wanted {n}")
            time.sleep(0.01)

    def drop_client(self) -> None:
        """Close the current client connection (daemon drops the client)."""
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()

    def stop(self) -> None:
        """Close listener and client; the port number stays ours for start()."""
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        self.drop_client()
        self._thread.join(timeout=5)

    def start(self) -> None:
        """Rebind the same port after stop() (a daemon restart)."""
        assert self._listener is None
        self._listener = socket.create_server(("127.0.0.1", self.port))
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._closed = True
        self.stop()


def call_in_thread(
    fn: Callable[..., Any], *args: Any, **kwargs: Any
) -> tuple[threading.Thread, dict]:
    """Run fn in a thread; the returned dict gains "value" or "error"."""
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - tests inspect the exception
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result
