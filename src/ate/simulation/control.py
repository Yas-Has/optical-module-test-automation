"""Helpers for a bench running as a separate program: ports, the control connection, start/stop."""

from __future__ import annotations

import logging
import os
import re
import shutil
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

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # the `ate` package itself
SRC_DIR = PACKAGE_DIR.parent                       # the folder holding it


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
    taken = busy_ports(sim.probe_host, wanted)
    if taken:
        raise BenchError(
            f"port(s) {', '.join(map(str, taken))} on {sim.probe_host} are already in use. "
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
        self._log_file = open(self.log_path, "w", encoding="utf-8")
        self._process = subprocess.Popen(
            self._command(), stdout=self._log_file, stderr=subprocess.STDOUT, env=self._env(),
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
            except BenchError:  # something accepted the connection but the bench isn't answering yet
                time.sleep(0.1)
                continue
            finally:
                client.close()
            log.info("simulator bench started (%s)", self._describe())
            return
        self.stop()
        raise BenchError(f"the simulator bench did not become ready within {ready_timeout_s:.0f} s; see {self.log_path}")

    def _command(self) -> list[str]:
        return [sys.executable, "-m", "ate", "bench", "--config", str(self.config_path)]

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")  # importable even if not installed
        return env

    def _describe(self) -> str:
        return f"pid {self._process.pid}"

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
        self._clean_up()

    def _clean_up(self) -> None:
        """Anything left over once the bench has stopped (nothing, for a local program)."""

    def _finish_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None


def check_docker_ready(config: AppConfig) -> None:
    """Docker installed, running, and the image built; otherwise a clear message saying which."""
    image = config.simulation.docker_image
    if not shutil.which("docker"):
        raise BenchError("simulation.mode is docker, but Docker isn't installed (the docker command was not found). "
                         "Install Docker Desktop, or Docker Engine on Linux, and try again.")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        raise BenchError("simulation.mode is docker, but Docker isn't running (or this user may not use it). "
                         "Start Docker Desktop, or on Linux: sudo systemctl start docker, and try again.")
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        raise BenchError(f"Docker image '{image}' not found. Build it once, in the project folder, with: docker build -t {image} .")


class DockerBenchProcess(BenchProcess):
    """``ate bench`` inside a Docker container, with its ports opened to this computer only.

    The container runs the code from this working copy (shared into it read-only), so the image
    only has to provide Python and the libraries; changing a simulator never needs a rebuild.
    """

    def start(self, ready_timeout_s: float = 60.0) -> None:
        check_docker_ready(self.config)
        self.container = "ate-bench-" + re.sub(r"[^a-zA-Z0-9_.-]", "-", self.log_path.parent.name)
        super().start(ready_timeout_s)

    def _command(self) -> list[str]:
        sim = self.config.simulation
        ports = [sim.control_port, *assign_ports(self.config).values()]
        published = [arg for port in ports for arg in ("-p", f"{sim.probe_host}:{port}:{port}")]
        return [
            "docker", "run", "--rm", "--init", "--name", self.container, *published,
            "-v", f"{PACKAGE_DIR}:/ate-src/ate:ro", "-e", "PYTHONPATH=/ate-src",  # only `ate`, never other libraries
            "-v", f"{self.config_path.resolve()}:/run-config/config.yaml:ro",
            sim.docker_image,
            "python", "-m", "ate", "bench", "--config", "/run-config/config.yaml",
            "--listen-host", "0.0.0.0", "--skip-port-check",
        ]

    def _env(self) -> dict[str, str]:
        return dict(os.environ)

    def _describe(self) -> str:
        return f"Docker container {self.container}"

    def _clean_up(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.container], capture_output=True)
