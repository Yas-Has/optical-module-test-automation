"""Helpers for a bench running as a separate program: ports, the control connection, start/stop."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ate.core.transport import parse_socket_address

if TYPE_CHECKING:
    from ate.config import AppConfig

log = logging.getLogger("ate.bench")


class BenchError(Exception):
    """The simulated bench could not be started or reached."""


# --------------------------------------------------------------------------- ports + addresses


def assign_ports(config: AppConfig) -> dict[str, int]:
    """Port for every instrument: 'auto' ones count up from first_instrument_port."""
    ports: dict[str, int] = {}
    next_port = config.simulation.first_instrument_port
    for inst in config.instruments:
        if inst.address == "auto":
            ports[inst.name] = next_port
            next_port += 1
        else:
            target = parse_socket_address(inst.address)
            if target is None:
                raise BenchError(
                    f"{inst.name}: the simulator can only listen on 'TCPIP::<host>::<port>::SOCKET' "
                    f"addresses (got '{inst.address}')"
                )
            ports[inst.name] = target[1]
    return ports


def instrument_address(config: AppConfig, name: str, ports: dict[str, int]) -> str:
    inst = config.instrument(name)
    if inst.address != "auto":
        return inst.address
    return f"TCPIP::{config.simulation.host}::{ports[name]}::SOCKET"


def busy_ports(host: str, ports: list[int]) -> list[int]:
    """Ports where something is already listening."""
    busy = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex((host, port)) == 0:
                busy.append(port)
    return busy


def check_ports_free(config: AppConfig) -> None:
    sim = config.simulation
    wanted = [sim.control_port, *assign_ports(config).values()]
    taken = busy_ports(sim.host, wanted)
    if taken:
        raise BenchError(
            f"port(s) {', '.join(map(str, taken))} on {sim.host} are already in use. "
            "Is another simulator bench still running? Stop it (Ctrl+C in its terminal), "
            "use --external-bench to connect to it, or change simulation.first_instrument_port / control_port."
        )


# --------------------------------------------------------------------------- control connection


class ControlClient:
    """Line-based connection to the bench's control port (not an instrument; absent with real hardware)."""

    def __init__(self, host: str, port: int, timeout_s: float = 5.0):
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout_s)
        except OSError as exc:
            raise BenchError(f"cannot reach the simulator bench control port {host}:{port}: {exc}") from exc
        self._file = self._sock.makefile("rw", encoding="utf-8", newline="\n")

    def send(self, line: str) -> str:
        try:
            self._file.write(line + "\n")
            self._file.flush()
            reply = self._file.readline()
        except OSError as exc:
            raise BenchError(f"lost contact with the simulator bench: {exc}") from exc
        if not reply:
            raise BenchError("the simulator bench closed the control connection")
        reply = reply.strip()
        if reply.startswith("ERR"):
            raise BenchError(f"bench refused '{line}': {reply}")
        return reply

    def close(self) -> None:
        for closer in (self._file.close, self._sock.close):
            try:
                closer()
            except OSError:
                pass


class NetworkBenchControl:
    """Same fault-forcing API as the in-process Bench, but over the control port."""

    def __init__(self, client: ControlClient):
        self._client = client

    def force_fault(self, name: str, kind: str, channel: int | None = None) -> None:
        self._client.send(f"SIM:FAULT {name} {kind}" + (f" {channel}" if channel is not None else ""))

    def kill(self, name: str, after_queries: int | None = None) -> None:
        self._client.send(f"SIM:KILL {name}" + (f" {after_queries}" if after_queries is not None else ""))


# --------------------------------------------------------------------------- background process


class BenchProcess:
    """Starts ``ate bench`` in the background and always stops it again."""

    def __init__(self, config: AppConfig, config_path: Path, log_path: Path):
        self.config = config
        self.config_path = config_path
        self.log_path = log_path
        self._process: subprocess.Popen | None = None
        self._log_file = None

    def start(self, ready_timeout_s: float = 15.0) -> None:
        check_ports_free(self.config)
        env = dict(os.environ)
        src_dir = str(Path(__file__).resolve().parents[2])  # make `ate` importable even if not installed
        env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")
        self._log_file = open(self.log_path, "w", encoding="utf-8")
        self._process = subprocess.Popen(
            [sys.executable, "-m", "ate", "bench", "--config", str(self.config_path)],
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        sim = self.config.simulation
        deadline = time.monotonic() + ready_timeout_s
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self._finish_log()
                raise BenchError(f"the simulator bench exited during start-up; see {self.log_path}")
            try:
                client = ControlClient(sim.host, sim.control_port, timeout_s=1.0)
            except BenchError:
                time.sleep(0.05)
                continue
            try:
                client.send("SIM:PING")
                log.info("simulator bench started (pid %d)", self._process.pid)
                return
            finally:
                client.close()
        self.stop()
        raise BenchError(f"the simulator bench did not become ready within {ready_timeout_s:.0f} s")

    def stop(self, control: ControlClient | None = None) -> None:
        if self._process is None:
            return
        try:
            if control is not None:
                control.send("SIM:SHUTDOWN")
        except BenchError:
            pass
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        log.info("simulator bench stopped")
        self._process = None
        self._finish_log()

    def _finish_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
