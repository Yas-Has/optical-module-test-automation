"""Shared pytest fixtures. Plain helper functions live in tests/helpers.py."""

from __future__ import annotations

import random
import socket

import pytest


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@pytest.fixture
def free_ports():
    """Returns a function giving a base port with `n` free consecutive ports after it."""
    def find(n: int) -> int:
        rng = random.Random()
        for _ in range(200):
            base = rng.randrange(20000, 60000)
            if all(_port_free(base + i) for i in range(n)):
                return base
        raise RuntimeError("no free ports found")
    return find
