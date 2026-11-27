"""ate/simulation/bench.py, control.py, loopback.py: the bench as a whole."""

from __future__ import annotations

import socket

import pytest

from ate.core.transport import CommTimeout, ConnectionLost
from ate.simulation.bench import Bench
from ate.simulation.control import BenchError, assign_ports, busy_ports, instrument_address
from ate.simulation.loopback import LoopbackTransport
from tests.helpers import build_config, load_data


@pytest.fixture
def bench(tmp_path):
    return Bench(build_config(tmp_path, clean=True), seed=42)


def test_one_simulator_per_configured_instrument(tmp_path):
    bench = Bench(build_config(tmp_path, load_data("examples/two_meters_16ch.yaml")), seed=1)
    assert sorted(bench.simulators) == ["opm_a", "opm_b", "tec_main"]
    assert bench.ports == {"tec_main": 5101, "opm_a": 5102, "opm_b": 5103}


def test_control_commands(bench):
    assert bench.handle_control("SIM:PING") == "OK"
    assert bench.handle_control("SIM:SEED?") == "42"
    assert bench.handle_control("SIM:ADVANCE 2.5") == "OK"
    assert bench.handle_control("SIM:TIME?") == "2.500000"
    assert bench.handle_control("SIM:PORTS?") == "tec_main=5101,opm_a=5102"
    assert bench.handle_control("SIM:FAULT opm_a channel_invalid 3") == "OK"
    assert bench.handle_control("SIM:KILL opm_a 5") == "OK"


def test_control_command_errors(bench):
    assert bench.handle_control("SIM:FLY") == "ERR:UNKNOWN_COMMAND"
    assert bench.handle_control("SIM:ADVANCE").startswith("ERR")
    assert bench.handle_control("SIM:FAULT nobody timeout").startswith("ERR")
    assert bench.handle_control("SIM:FAULT opm_a meteor").startswith("ERR")


def test_forced_fault_reaches_the_instrument(bench):
    bench.force_fault("opm_a", "timeout")
    assert bench.simulator("opm_a").handle("POW:ALL?").text is None


# --------------------------------------------------------------------------- ports + addresses


def test_ports_auto_and_explicit(tmp_path):
    data = load_data()
    data["instruments"][0]["address"] = "TCPIP::127.0.0.1::6000::SOCKET"
    config = build_config(tmp_path, data)
    ports = assign_ports(config)
    assert ports == {"tec_main": 6000, "opm_a": 5101}
    assert instrument_address(config, "opm_a", ports) == "TCPIP::127.0.0.1::5101::SOCKET"


def test_simulator_cannot_listen_on_a_non_network_address(tmp_path):
    data = load_data()
    data["instruments"][0]["address"] = "GPIB0::12::INSTR"
    with pytest.raises(BenchError, match="can only listen"):
        assign_ports(build_config(tmp_path, data))


@pytest.mark.network
def test_busy_ports_are_detected(free_ports):
    port = free_ports(2)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen()
        assert busy_ports("127.0.0.1", [port, port + 1]) == [port]


# --------------------------------------------------------------------------- in-process transport


def test_loopback_reply_timeout_and_lost_connection(bench):
    transport = LoopbackTransport(bench.simulator("opm_a"))
    assert transport.query("*IDN?").startswith("XSIM,OPM-8")
    bench.force_fault("opm_a", "timeout")
    with pytest.raises(CommTimeout):
        transport.query("POW:ALL?")
    bench.kill("opm_a")
    with pytest.raises(ConnectionLost):
        transport.query("POW:ALL?")
    with pytest.raises(ConnectionLost):
        transport.query("*IDN?")  # stays closed


# --------------------------------------------------------------------------- docker mode (no Docker needed)


def _docker_bench(tmp_path, **overrides):
    from ate.simulation.control import DockerBenchProcess
    config = build_config(tmp_path, mode="docker", **overrides)
    (tmp_path / "run").mkdir(exist_ok=True)
    return DockerBenchProcess(config, tmp_path / "config_used.yaml", tmp_path / "run" / "bench.log")


def test_docker_bench_opens_every_port_to_this_computer_only(tmp_path):
    process = _docker_bench(tmp_path)
    process.container = "ate-bench-test"
    command = process._command()
    published = [command[i + 1] for i, arg in enumerate(command) if arg == "-p"]
    assert published == ["127.0.0.1:5100:5100", "127.0.0.1:5101:5101", "127.0.0.1:5102:5102"]
    assert command[-3:] == ["--listen-host", "0.0.0.0", "--skip-port-check"]
    mounts = [command[i + 1] for i, arg in enumerate(command) if arg == "-v"]
    assert mounts[0].endswith("/ate:/ate-src/ate:ro")  # only our own package goes in, never other libraries


def test_docker_bench_explains_what_is_missing(tmp_path, monkeypatch):
    import subprocess
    from ate.simulation import control

    monkeypatch.setattr(control.shutil, "which", lambda name: None)
    with pytest.raises(BenchError, match="Docker isn't installed"):
        _docker_bench(tmp_path).start()

    monkeypatch.setattr(control.shutil, "which", lambda name: "/usr/bin/docker")
    not_running = subprocess.CompletedProcess([], 1)
    monkeypatch.setattr(control.subprocess, "run", lambda *a, **k: not_running)
    with pytest.raises(BenchError, match="Docker isn't running"):
        _docker_bench(tmp_path).start()

    answers = iter([subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1)])
    monkeypatch.setattr(control.subprocess, "run", lambda *a, **k: next(answers))
    with pytest.raises(BenchError, match="docker build -t"):
        _docker_bench(tmp_path).start()


def test_a_docker_run_without_docker_stops_before_creating_anything(tmp_path, monkeypatch, free_ports):
    from ate.sequence.runner import TestRunner
    from ate.simulation import control

    monkeypatch.setattr(control.shutil, "which", lambda name: None)
    base = free_ports(3)
    config = build_config(tmp_path, mode="docker", simulation__control_port=base,
                          simulation__first_instrument_port=base + 1)
    with pytest.raises(BenchError, match="Docker isn't installed"):
        TestRunner(config, console=False).run()
    assert not (tmp_path / "results").exists()
