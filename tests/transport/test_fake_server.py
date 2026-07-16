# ABOUTME: Smoke test for the FakeServer test support: raw-socket request
# ABOUTME: gets a daemon-style (bin-packed) reply; drop/stop/start work.
import socket

import msgpack

from tests.support import FakeServer


def test_fake_server_speaks_the_wire_protocol(server: FakeServer) -> None:
    with socket.create_connection(("127.0.0.1", server.port)) as sock:
        sock.sendall(msgpack.packb([4, 1, "10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"]]))
        unpacker = msgpack.Unpacker()
        while True:
            unpacker.feed(sock.recv(65536))
            try:
                reply = unpacker.unpack()
                break
            except msgpack.exceptions.OutOfData:
                continue
    assert reply == [4 | 0x10, 1, [[b"1.3.6.1.2.1.1.5.0", b"fake.example.net"]]]
    assert server.requests == [[4, 1, "10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"]]]


def test_fake_server_stop_start_same_port(server: FakeServer) -> None:
    port = server.port
    server.stop()
    server.start()
    assert server.port == port
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        server.wait_connections(1)
