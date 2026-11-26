"""`ate run --mode docker`: the simulator bench in a Docker container, the test program on this computer.

Not part of the normal test run (needs Docker; about 15 s). Run it with:
    pytest -m docker
It is skipped if Docker isn't installed or running.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
from pathlib import Path

import pytest

from ate.sequence.runner import COMPLETE
from tests.helpers import build_config, run

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "optical-module-test-automation"
NOT_COMPARED = {"run_id", "timestamp_utc"}


def rows(run_dir: Path, name: str) -> list[dict]:
    with open(run_dir / name, newline="", encoding="utf-8") as f:
        return [{k: v for k, v in r.items() if k not in NOT_COMPARED} for r in csv.DictReader(f)]


@pytest.fixture(scope="module")
def image():
    if not shutil.which("docker") or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("Docker is not installed or not running")
    subprocess.run(["docker", "build", "-t", IMAGE, "."], cwd=ROOT, capture_output=True, timeout=600, check=True)
    return IMAGE


def test_a_regular_run_with_mode_docker(tmp_path, image, free_ports):
    base = free_ports(3)
    in_docker = run(build_config(tmp_path, seed=42, mode="docker", simulation__control_port=base,
                                 simulation__first_instrument_port=base + 1))
    assert in_docker.status == COMPLETE
    assert "Docker container ate-bench-" in (in_docker.run_dir / "run.log").read_text()
    local = run(build_config(tmp_path, seed=42))
    for name in ("raw_measurements.csv", "summary.csv"):
        assert rows(in_docker.run_dir, name) == rows(local.run_dir, name), name
    leftover = subprocess.run(["docker", "ps", "-aq", "--filter", f"name={in_docker.run_dir.name}"],
                              capture_output=True, text=True).stdout
    assert leftover.strip() == ""  # the container is removed after the run
