"""The simulated test bench.

One virtual device, one simulator per configured instrument, each listening on its own port,
plus a control port for the simulated clock and for forcing faults in automated tests.

Everything runs on a single asyncio event loop, so two instruments can never act on the shared
device at the same moment: no locks needed, and behaviour stays reproducible.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections import defaultdict
from collections.abc import Callable

from ate.config import AppConfig
from ate.core import registry
from ate.simulation.simulator import InstrumentSimulator
from ate.simulation.clock import SimClock
from ate.simulation.control import BenchError, assign_ports
from ate.simulation.device import VirtualDevice

log = logging.getLogger("ate.bench")


class Bench:
    def __init__(self, config: AppConfig, seed: int):
        self.config = config
        self.seed = seed
        self.clock = SimClock()
        self.device = VirtualDevice(config.simulation.device)
        self.simulators: dict[str, InstrumentSimulator] = {}
        for inst in config.instruments:
            entry = registry.get(inst.type)
            if entry.simulator is None:
                raise BenchError(f"instrument type '{inst.type}' has no simulator")
            self.simulators[inst.name] = entry.simulator(inst, self.device, self.clock, seed)
        self.ports = assign_ports(config)
        self._servers: dict[str, asyncio.Server] = {}
        self._writers: dict[str, set[asyncio.StreamWriter]] = defaultdict(set)
        self._stop: asyncio.Event | None = None

    # ----------------------------------------------------------------- test hooks (also via control port)

    def simulator(self, name: str) -> InstrumentSimulator:
        try:
            return self.simulators[name]
        except KeyError:
            raise KeyError(f"no instrument named '{name}' on this bench") from None

    def force_fault(self, name: str, kind: str, channel: int | None = None) -> None:
        self.simulator(name).faults.force(kind, channel)

    def kill(self, name: str, after_queries: int | None = None) -> None:
        self.simulator(name).kill(after_queries)

    def handle_control(self, line: str) -> str:
        parts = line.split()
        if not parts:
            return "ERR:EMPTY_COMMAND"
        command, args = parts[0].upper(), parts[1:]
        try:
            if command == "SIM:PING":
                return "OK"
            if command == "SIM:TIME?":
                return f"{self.clock.now:.6f}"
            if command == "SIM:SEED?":
                return str(self.seed)
            if command == "SIM:ADVANCE":
                self.clock.advance(float(args[0]))
                return "OK"
            if command == "SIM:FAULT":
                self.force_fault(args[0], args[1], int(args[2]) if len(args) > 2 else None)
                return "OK"
            if command == "SIM:KILL":
                self.kill(args[0], int(args[1]) if len(args) > 1 else None)
                return "OK"
            if command == "SIM:PORTS?":
                return ",".join(f"{name}={port}" for name, port in self.ports.items())
        except (IndexError, ValueError, KeyError) as exc:
            return f"ERR:{exc}"
        return "ERR:UNKNOWN_COMMAND"

    # ----------------------------------------------------------------- network serving

    def run(self, announce: Callable[[str], None] = print) -> None:
        """Serve until SIM:SHUTDOWN or Ctrl+C."""
        try:
            asyncio.run(self.serve(announce))
        except KeyboardInterrupt:
            announce("bench stopped (Ctrl+C)")

    async def serve(self, announce: Callable[[str], None] | None = None) -> None:
        self._stop = asyncio.Event()
        host = self.config.simulation.bind_host
        control = None
        try:
            for name in self.simulators:
                self._servers[name] = await asyncio.start_server(
                    functools.partial(self._serve_instrument, name), host, self.ports[name]
                )
            control = await asyncio.start_server(self._serve_control, host, self.config.simulation.control_port)
        except OSError as exc:
            for server in self._servers.values():
                server.close()
            raise BenchError(f"could not open a port on {host}: {exc}") from exc

        if announce:
            announce(f"simulator bench ready on {host} (seed {self.seed})")
            announce(f"  control port: {self.config.simulation.control_port}")
            for name, port in self.ports.items():
                announce(f"  {name}: TCPIP::{self.config.simulation.host}::{port}::SOCKET")
        try:
            await self._stop.wait()
        finally:
            control.close()
            for name in list(self._servers):
                self._close_instrument(name)

    def _close_instrument(self, name: str) -> None:
        server = self._servers.pop(name, None)
        if server is not None:
            server.close()  # stop accepting new connections
        for writer in list(self._writers[name]):
            writer.close()  # drop existing ones

    async def _serve_instrument(self, name: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        simulator = self.simulators[name]
        scale = self.config.simulation.time_scale
        self._writers[name].add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                reply = simulator.handle(text)
                if reply.killed:
                    log.warning("%s: simulated instrument failure, dropping connection", name)
                    self._close_instrument(name)
                    break
                if reply.delay_s and scale:
                    await asyncio.sleep(reply.delay_s * scale)
                if reply.text is not None:  # None = stay silent, so the client's real timeout fires
                    writer.write((reply.text + "\n").encode("utf-8"))
                    await writer.drain()
        except ConnectionError:
            pass
        finally:
            self._writers[name].discard(writer)
            writer.close()

    async def _serve_control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                if text.upper() == "SIM:SHUTDOWN":
                    writer.write(b"OK\n")
                    await writer.drain()
                    self._stop.set()
                    break
                writer.write((self.handle_control(text) + "\n").encode("utf-8"))
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()
