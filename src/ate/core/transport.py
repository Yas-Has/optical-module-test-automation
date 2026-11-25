"""Transports move one line of text to an instrument and one line back.

They know nothing about instruments. Two implementations exist:

* ``VisaTransport`` (here): PyVISA over the network, for the simulator bench *and* real hardware
* ``LoopbackTransport`` (``ate.simulation.loopback``): calls a simulator directly, for fast tests
"""

from __future__ import annotations

import logging
import re
import socket
from abc import ABC, abstractmethod

log = logging.getLogger("ate.transport")


class CommTimeout(Exception):
    """The instrument did not answer in time. Retryable."""


class ConnectionLost(Exception):
    """The instrument cannot be reached at all. Not retryable."""


class Transport(ABC):
    @abstractmethod
    def query(self, command: str) -> str:
        """Send one command, return the reply line (without line ending)."""

    def clear(self) -> None:
        """Discard anything left over in the input buffer (called before retries)."""

    @abstractmethod
    def close(self) -> None: ...


_SOCKET_ADDRESS = re.compile(r"^TCPIP\d*::([^:]+)::(\d+)::SOCKET$", re.IGNORECASE)


def parse_socket_address(address: str) -> tuple[str, int] | None:
    """'TCPIP::127.0.0.1::5101::SOCKET' -> ('127.0.0.1', 5101); anything else -> None."""
    match = _SOCKET_ADDRESS.match(address.strip())
    return (match.group(1), int(match.group(2))) if match else None


class VisaTransport(Transport):
    """PyVISA with the pure-Python ``pyvisa-py`` backend (no vendor drivers needed)."""

    _resource_manager = None

    def __init__(self, address: str, timeout_ms: int):
        import pyvisa

        self.address = address
        self._pyvisa = pyvisa
        try:
            if VisaTransport._resource_manager is None:
                VisaTransport._resource_manager = pyvisa.ResourceManager("@py")
            self._resource = VisaTransport._resource_manager.open_resource(
                address, read_termination="\n", write_termination="\n", timeout=timeout_ms
            )
        except Exception as exc:  # pyvisa raises several unrelated types here
            raise ConnectionLost(f"could not connect to {address}: {exc}") from exc

    def query(self, command: str) -> str:
        errors = self._pyvisa.errors
        try:
            return self._resource.query(command)
        except errors.VisaIOError as exc:
            if exc.error_code == self._pyvisa.constants.StatusCode.error_timeout:
                # A timeout and a vanished instrument look the same from here.
                # Knock on the door: if nobody is listening any more, it is a lost connection.
                if not self._reachable():
                    raise ConnectionLost(f"{self.address} is no longer reachable") from exc
                raise CommTimeout(f"no reply to '{command}' from {self.address}") from exc
            raise ConnectionLost(f"{self.address}: {exc}") from exc
        except (OSError, EOFError) as exc:
            raise ConnectionLost(f"{self.address}: {exc}") from exc

    def _reachable(self) -> bool:
        target = parse_socket_address(self.address)
        if target is None:
            return True
        try:
            with socket.create_connection(target, timeout=0.5):
                return True
        except OSError:
            return False

    def clear(self) -> None:
        try:
            self._resource.clear()
        except Exception:  # not every resource type supports clear(); harmless
            pass

    def close(self) -> None:
        try:
            self._resource.close()
        except Exception as exc:
            log.debug("closing %s: %s", self.address, exc)
