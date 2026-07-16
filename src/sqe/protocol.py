# ABOUTME: Sans-I/O protocol core for the snmp-query-engine client protocol:
# ABOUTME: request encoding, response decoding and matching, SETOPT replay cache.

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import msgpack

from .errors import SqeError

SETOPT = 1
GETOPT = 2
INFO = 3
GET = 4
GETTABLE = 5
DEST_INFO = 6


@dataclass(frozen=True)
class VarBind:
    """One OID/value pair from a GET or GETTABLE reply.

    Per-OID wire errors ("timeout", "no-such-object", ...) land in `error`;
    they never raise.
    """

    oid: str
    value: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class Response:
    """A decoded daemon reply, matched to the request id that caused it."""

    request_id: int
    value: Any = None
    error: SqeError | None = None


_REPLY_OK = 0x10
_REPLY_ERROR = 0x20

_HEXSTRING_OPTIONS = ("engineid", "authkul", "privkul")
_V3_OPTIONS = frozenset(
    {
        "engineid",
        "username",
        "authprotocol",
        "authpassword",
        "authkul",
        "privprotocol",
        "privpassword",
        "privkul",
    }
)


@dataclass
class _Pending:
    """Bookkeeping for one request awaiting its reply."""

    rtype: int
    host: str | None = None
    port: int | None = None
    options: dict[str, Any] | None = None


class Connection:
    """Sans-I/O protocol state machine.

    Encodes requests to wire bytes and decodes/matches wire bytes fed back
    in; never touches a socket. Transports own all I/O.
    """

    def __init__(self) -> None:
        self._next_id = 1
        self._pending: dict[int, _Pending] = {}
        self._tombstones: set[int] = set()
        self._option_cache: dict[tuple[str, int], dict[str, Any]] = {}
        self._unpacker = msgpack.Unpacker()

    def _send(self, pending: _Pending, tail: list[Any]) -> tuple[int, bytes]:
        request_id = self._next_id
        self._next_id += 1
        self._pending[request_id] = pending
        data: bytes = msgpack.packb([pending.rtype, request_id, *tail])
        return request_id, data

    def send_setopt(self, host: str, port: int, options: dict[str, Any]) -> tuple[int, bytes]:
        encoded = dict(options)
        for name in _HEXSTRING_OPTIONS:
            value = encoded.get(name)
            if isinstance(value, bytes):
                encoded[name] = value.hex()
        pending = _Pending(SETOPT, host, port, options=encoded)
        return self._send(pending, [host, port, encoded])

    def send_getopt(self, host: str, port: int) -> tuple[int, bytes]:
        return self._send(_Pending(GETOPT, host, port), [host, port])

    def send_info(self) -> tuple[int, bytes]:
        return self._send(_Pending(INFO), [])

    def send_get(self, host: str, port: int, oids: Iterable[str]) -> tuple[int, bytes]:
        return self._send(_Pending(GET, host, port), [host, port, list(oids)])

    def send_gettable(
        self, host: str, port: int, oid: str, max_repetitions: int | None = None
    ) -> tuple[int, bytes]:
        tail: list[Any] = [host, port, oid]
        if max_repetitions is not None:
            tail.append(max_repetitions)
        return self._send(_Pending(GETTABLE, host, port), tail)

    def send_dest_info(self, host: str, port: int) -> tuple[int, bytes]:
        return self._send(_Pending(DEST_INFO, host, port), [host, port])
