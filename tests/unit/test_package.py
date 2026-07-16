# ABOUTME: Package-level smoke test: the sqe package imports and carries
# ABOUTME: a version string.
import sqe


def test_version() -> None:
    assert isinstance(sqe.__version__, str)
    assert sqe.__version__ != ""
