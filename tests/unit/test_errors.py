# ABOUTME: Tests for the exception hierarchy and the VarBind/Response
# ABOUTME: dataclasses of the protocol core.
import dataclasses

import pytest

from sqe.errors import ConnectionLost, ProtocolError, RequestError, SqeError
from sqe.protocol import DEST_INFO, GET, GETOPT, GETTABLE, INFO, SETOPT, Response, VarBind


def test_exception_hierarchy() -> None:
    for exc in (RequestError, ConnectionLost, ProtocolError):
        assert issubclass(exc, SqeError)
    assert issubclass(SqeError, Exception)


def test_request_error_carries_message() -> None:
    err = RequestError("bad IP address")
    assert str(err) == "bad IP address"


def test_varbind_ok() -> None:
    good = VarBind("1.3.6.1.2.1.1.5.0", value=b"host.example.net")
    bad = VarBind("1.3.6.1.2.1.1.5.0", error="timeout")
    assert good.ok
    assert good.value == b"host.example.net"
    assert good.error is None
    assert not bad.ok
    assert bad.error == "timeout"


def test_varbind_is_frozen() -> None:
    vb = VarBind("1.3.6.1.2.1.1.5.0", value=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        vb.value = 2  # type: ignore[misc]


def test_varbind_equality() -> None:
    assert VarBind("1.3", value=5) == VarBind("1.3", value=5)
    assert VarBind("1.3", value=5) != VarBind("1.3", error="timeout")


def test_response_defaults() -> None:
    resp = Response(7, value={"a": 1})
    assert resp.request_id == 7
    assert resp.error is None


def test_request_type_constants() -> None:
    assert (SETOPT, GETOPT, INFO, GET, GETTABLE, DEST_INFO) == (1, 2, 3, 4, 5, 6)
