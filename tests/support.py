# ABOUTME: Shared test helpers: daemon-style reply packing (all strings as
# ABOUTME: msgpack bin), an in-process fake daemon, and thread-call helpers.
from __future__ import annotations

from typing import Any

import msgpack


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
