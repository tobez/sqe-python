# ABOUTME: Sans-I/O protocol core for the snmp-query-engine client protocol:
# ABOUTME: request encoding, response decoding and matching, SETOPT replay cache.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
