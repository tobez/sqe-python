# ABOUTME: Exception hierarchy for the sqe client library.
# ABOUTME: All sqe exceptions derive from SqeError.


class SqeError(Exception):
    """Base class for all sqe errors."""


class RequestError(SqeError):
    """The daemon rejected a request; carries the daemon's error message."""


class ConnectionLost(SqeError):
    """The connection to the daemon was lost while the request was in flight."""


class ProtocolError(SqeError):
    """The daemon sent something the protocol does not allow."""
