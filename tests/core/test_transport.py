"""ate/core/transport.py: PyVISA over a real local network socket."""

from __future__ import annotations

import socketserver
import threading

import pytest

from ate.core.transport import CommTimeout, ConnectionLost, VisaTransport, parse_socket_address


@pytest.mark.parametrize("address, expected", [
    ("TCPIP::127.0.0.1::5101::SOCKET", ("127.0.0.1", 5101)),
    ("tcpip0::lab-tec.local::5025::socket", ("lab-tec.local", 5025)),
    ("GPIB0::12::INSTR", None),
    ("auto", None),
])
def test_parse_socket_address(address, expected):
    assert parse_socket_address(address) == expected


class _Instrument(socketserver.StreamRequestHandler):
    """A tiny fake instrument: answers PING, stays silent for SILENT."""

    def handle(self):
        for line in self.rfile:
            command = line.decode().strip()
            if command == "PING":
                self.wfile.write(b"PONG\n")


@pytest.fixture
def server():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Instrument)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def address(srv) -> str:
    return f"TCPIP::127.0.0.1::{srv.server_address[1]}::SOCKET"


@pytest.mark.network
def test_query_gets_the_reply_line(server):
    transport = VisaTransport(address(server), timeout_ms=500)
    assert transport.query("PING") == "PONG"
    transport.close()


@pytest.mark.network
def test_silence_is_a_timeout(server):
    transport = VisaTransport(address(server), timeout_ms=200)
    with pytest.raises(CommTimeout):
        transport.query("SILENT")
    assert transport.query("PING") == "PONG"  # still usable afterwards
    transport.close()


@pytest.mark.network
def test_vanished_instrument_is_a_lost_connection_not_a_timeout(server):
    transport = VisaTransport(address(server), timeout_ms=200)
    server.shutdown()
    server.server_close()  # nobody listening any more
    with pytest.raises(ConnectionLost):
        transport.query("SILENT")
    transport.close()


@pytest.mark.network
def test_nothing_listening_is_reported_on_first_contact(free_ports):
    # pyvisa-py opens socket resources lazily, so the problem shows up on the first command
    # (for an instrument, always the *IDN? sent by connect()) as a lost connection, never a hang.
    transport = VisaTransport(f"TCPIP::127.0.0.1::{free_ports(1)}::SOCKET", timeout_ms=200)
    with pytest.raises(ConnectionLost):
        transport.query("*IDN?")
    transport.close()
