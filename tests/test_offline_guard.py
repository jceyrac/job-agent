"""Prove the offline socket guard (tests/conftest.py) blocks non-loopback connects.

spec 044b FR-004 / research R3: the suite must be offline, enforced. These two
tests pin the behaviour — a non-loopback connect raises the guard's error, while
a loopback connect still works (so `TestClient`/ASGI transport and any
loopback-bound listener are unaffected).
"""
import socket

import pytest


def test_non_loopback_connect_is_blocked():
    with pytest.raises(ConnectionError, match="offline guard"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(
            ("93.184.216.34", 80)  # example.com — a real, non-loopback host
        )


def test_loopback_connect_still_works():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", server.getsockname()[1]))
        client.close()
    finally:
        server.close()
