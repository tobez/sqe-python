# ABOUTME: Sans-I/O protocol core for the snmp-query-engine client protocol:
# ABOUTME: request encoding, response decoding and matching, SETOPT replay cache.

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import msgpack

from .errors import ProtocolError, RequestError, SqeError

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


def _text(value: bytes | str) -> str:
    """Decode bytes to string, mirroring daemon's bin-everything strategy."""
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"undecodable string from daemon: {value!r}") from exc


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

    def feed(self, data: bytes) -> None:
        """Give the connection bytes read from the wire."""
        self._unpacker.feed(data)

    def next_response(self) -> Response | None:
        """Return the next decoded response, or None if more bytes are needed.

        Call repeatedly after each feed(): one feed can complete zero or
        more responses. Raises ProtocolError on anything malformed;
        transports must treat that as connection-fatal.
        """
        while True:
            try:
                obj = self._unpacker.unpack()
            except msgpack.exceptions.OutOfData:
                return None
            except (msgpack.exceptions.UnpackException, ValueError) as exc:
                raise ProtocolError(f"malformed msgpack from daemon: {exc}") from exc
            response = self._handle(obj)
            if response is not None:
                return response

    def _handle(self, obj: Any) -> Response | None:
        """Process a decoded msgpack object, returning a Response or None if skipped."""
        if not isinstance(obj, list) or len(obj) < 2:
            raise ProtocolError(f"response is not a well-formed array: {obj!r}")
        rtype, request_id = obj[0], obj[1]
        if not isinstance(rtype, int) or not isinstance(request_id, int):
            raise ProtocolError(f"response type/id are not integers: {obj!r}")
        if request_id in self._tombstones:
            self._tombstones.discard(request_id)
            return None
        pending = self._pending.get(request_id)
        if pending is None:
            raise ProtocolError(f"response for unknown request id {request_id}")
        if rtype == pending.rtype | _REPLY_ERROR:
            del self._pending[request_id]
            if len(obj) != 3 or not isinstance(obj[2], (bytes, str)):
                raise ProtocolError(f"malformed error reply: {obj!r}")
            return Response(request_id, error=RequestError(_text(obj[2])))
        if rtype != pending.rtype | _REPLY_OK:
            raise ProtocolError(
                f"reply type 0x{rtype:x} does not match request type {pending.rtype}"
            )
        del self._pending[request_id]
        if len(obj) != 3:
            raise ProtocolError(f"success reply is not a 3-element array: {obj!r}")
        value = self._decode_payload(pending.rtype, obj[2])
        if pending.rtype == SETOPT:
            self._cache_setopt(pending)
        return Response(request_id, value=value)

    def _decode_payload(self, rtype: int, payload: Any) -> Any:
        """Decode a payload based on response type."""
        if rtype in (GET, GETTABLE):
            return self._decode_varbinds(payload)
        return self._decode_map(payload)

    def _decode_map(self, payload: Any) -> dict[str, Any]:
        """Decode a map, normalizing all keys to strings recursively."""
        if not isinstance(payload, dict):
            raise ProtocolError(f"reply payload is not a map: {payload!r}")
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if not isinstance(key, (bytes, str)):
                raise ProtocolError(f"non-string map key in reply: {key!r}")
            out[_text(key)] = self._decode_map(value) if isinstance(value, dict) else value
        return out

    def _decode_varbinds(self, payload: Any) -> list[VarBind]:
        """Decode a list of OID/value pairs (GET/GETTABLE result)."""
        if not isinstance(payload, list):
            raise ProtocolError(f"oid reply payload is not an array: {payload!r}")
        out: list[VarBind] = []
        for row in payload:
            if not isinstance(row, list) or len(row) != 2 or not isinstance(row[0], (bytes, str)):
                raise ProtocolError(f"malformed varbind row: {row!r}")
            oid, value = _text(row[0]), row[1]
            if isinstance(value, list):
                if len(value) != 1 or not isinstance(value[0], (bytes, str)):
                    raise ProtocolError(f"malformed per-oid error for {oid}: {value!r}")
                out.append(VarBind(oid, error=_text(value[0])))
            else:
                out.append(VarBind(oid, value=value))
        return out

    def _cache_setopt(self, pending: _Pending) -> None:
        """Cache SETOPT options for this host/port pair."""
        assert pending.host is not None and pending.port is not None
        assert pending.options is not None
        cache = self._option_cache.setdefault((pending.host, pending.port), {})
        if _V3_OPTIONS & pending.options.keys():
            for name in _V3_OPTIONS:
                cache.pop(name, None)
        cache.update(pending.options)
