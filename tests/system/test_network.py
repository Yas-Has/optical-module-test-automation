"""Whole program over the real network: the bench as a separate program, drivers using PyVISA."""

from __future__ import annotations

import socket

import pytest

from ate.sequence.runner import COMPLETE, COMPLETE_WITH_ERRORS, TestRunner
from ate.simulation.control import BenchError
from tests.helpers import build_config, channel, comparable, load_data, network_config, rows_at, run

pytestmark = pytest.mark.network


def test_network_run_is_identical_to_in_process(tmp_path, free_ports):
    in_process = run(build_config(tmp_path, seed=42))
    network = run(network_config(tmp_path, free_ports(3), seed=42))
    assert network.status == COMPLETE
    assert comparable(network) == comparable(in_process)
    assert "simulator bench ready" in (network.run_dir / "bench.log").read_text()


def test_bench_listening_everywhere_is_reached_by_name(tmp_path, free_ports):
    # The Docker setup in miniature: the bench listens on every address, the tester connects by name.
    in_process = run(build_config(tmp_path, seed=42))
    network = run(network_config(tmp_path, free_ports(3), seed=42,
                                 simulation__host="localhost", simulation__listen_host="0.0.0.0"))
    assert network.status == COMPLETE and comparable(network) == comparable(in_process)
    assert "simulator bench ready on 0.0.0.0" in (network.run_dir / "bench.log").read_text()


def test_timeout_is_a_real_timeout(tmp_path, free_ports):
    data = load_data()
    data["instruments"][1]["timeout_ms"] = 300
    config = network_config(tmp_path, free_ports(3), data, clean=True)
    result = TestRunner(config, console=False,
                        bench_hook=lambda control: control.force_fault("opm_a", "timeout")).run()
    assert [r["status"] for r in rows_at(result, 1) if r["attempt"] == "1"] == ["TIMEOUT"] * 8
    assert result.overall_result == "PASS"


def test_lost_meter_is_detected(tmp_path, free_ports):
    data = load_data("examples/two_meters_16ch.yaml")
    for inst in data["instruments"]:
        inst["timeout_ms"] = 300
    config = network_config(tmp_path, free_ports(4), data, clean=True)
    result = TestRunner(config, console=False, bench_hook=lambda control: control.kill("opm_b", 10)).run()
    assert result.status == COMPLETE_WITH_ERRORS
    assert channel(result, 9).valid_count == 10 and channel(result, 1).result == "PASS"


def test_busy_port_is_reported_before_anything_is_created(tmp_path, free_ports):
    base = free_ports(3)
    config = network_config(tmp_path, base)
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", base + 1))
        blocker.listen()
        with pytest.raises(BenchError, match="already in use"):
            TestRunner(config, console=False).run()
    assert not (tmp_path / "results").exists()
