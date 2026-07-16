# ABOUTME: Session fixtures for the live-daemon integration tier: probes the
# ABOUTME: snmp-query-engine binary, spawns snmpsim and the daemon, allocates ports.
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import sqe

DATA_DIR = Path(__file__).parent / "data"
SYSNAME_OID = "1.3.6.1.2.1.1.5.0"


def find_daemon() -> str | None:
    """Probe order: $SQE_BINARY -> PATH -> ../snmp-query-engine sibling checkout."""
    env = os.environ.get("SQE_BINARY")
    if env:
        return env if os.access(env, os.X_OK) else None
    which = shutil.which("snmp-query-engine")
    if which:
        return which
    repo_root = Path(__file__).resolve().parents[2]
    sibling = repo_root.parent / "snmp-query-engine" / "snmp-query-engine"
    if os.access(sibling, os.X_OK):
        return str(sibling)
    return None


def free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def stop_child(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)


class SqeDaemon:
    """Owns a snmp-query-engine child on a fixed port; restartable in place."""

    def __init__(self, binary: str, port: int, log_path: Path) -> None:
        self.binary = binary
        self.port = port
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        with open(self.log_path, "ab") as log:
            self.proc = subprocess.Popen(
                [self.binary, "-q", "-p", str(self.port)],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + 10
        while True:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"daemon exited with {self.proc.returncode} during startup; log:\n"
                    + self.log_path.read_text()
                )
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    pass
            except OSError:
                if time.monotonic() > deadline:
                    stop_child(self.proc)
                    self.proc = None
                    raise RuntimeError(
                        "daemon never started listening; log:\n" + self.log_path.read_text()
                    ) from None
                time.sleep(0.05)
                continue
            # The port answering is NOT enough: a stale/foreign listener can
            # answer while our child died on EADDRINUSE. The child must ALSO
            # still be alive after the successful connect.
            if self.proc.poll() is not None:
                raise RuntimeError(
                    "daemon died right after the port opened (foreign listener on "
                    f"port {self.port}?); log:\n" + self.log_path.read_text()
                )
            return

    def stop(self) -> None:
        if self.proc is not None:
            stop_child(self.proc)
            self.proc = None

    def restart(self) -> None:
        self.stop()
        self.start()


@pytest.fixture(scope="session")
def daemon_binary() -> str:
    binary = find_daemon()
    if binary is None:
        pytest.skip("snmp-query-engine binary not found (set SQE_BINARY)")
    return binary


@pytest.fixture(scope="session")
def daemon(daemon_binary: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[SqeDaemon]:
    log_path = tmp_path_factory.mktemp("sqe-daemon") / "daemon.log"
    child = SqeDaemon(daemon_binary, free_port(socket.SOCK_STREAM), log_path)
    child.start()
    yield child
    child.stop()


@pytest.fixture(scope="session")
def snmpsim_agent(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, int]]:
    responder = Path(sys.executable).parent / "snmpsim-command-responder"
    if not responder.exists():
        # snmpsim is a dev-group dependency: its absence is an environment
        # bug, never a reason to skip the tier.
        raise RuntimeError(f"snmpsim-command-responder not found at {responder}")
    port = free_port(socket.SOCK_DGRAM)
    log_path = tmp_path_factory.mktemp("snmpsim") / "snmpsim.log"
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            [
                str(responder),
                f"--data-dir={DATA_DIR}",
                f"--agent-udpv4-endpoint=127.0.0.1:{port}",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    # No port probe here: `target` already does end-to-end readiness. This
    # is just a cheap check that the child didn't die immediately (e.g. a
    # bad CLI arg), so that failure is reported directly instead of as an
    # indirect 30s "snmpsim never answered" from `target`.
    time.sleep(0.2)
    if proc.poll() is not None:
        raise RuntimeError(
            f"snmpsim exited with {proc.returncode} during startup; log:\n" + log_path.read_text()
        )
    yield ("127.0.0.1", port)
    stop_child(proc)


@pytest.fixture(scope="session")
def target(daemon: SqeDaemon, snmpsim_agent: tuple[str, int]) -> tuple[str, int]:
    """The snmpsim agent (host, port), readiness-checked through the daemon.

    snmpsim startup is slow (first run may compile data indices), so retry
    with a short SNMP timeout until the canned sysName comes back.
    """
    host, port = snmpsim_agent
    deadline = time.monotonic() + 30
    with sqe.Client("127.0.0.1", daemon.port) as client:
        client.setopt(host, port, {"timeout": 500, "retries": 1})
        while True:
            varbinds = client.get(host, port, [SYSNAME_OID], timeout=10)
            if varbinds[0].ok:
                return (host, port)
            if time.monotonic() > deadline:
                raise RuntimeError(f"snmpsim never answered: {varbinds[0].error}")


@pytest.fixture
def mute_udp_port() -> Iterator[int]:
    """A UDP port that is bound but never answers: guaranteed silent drop
    (no ICMP port-unreachable), which is what makes the daemon time out."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    yield sock.getsockname()[1]
    sock.close()
