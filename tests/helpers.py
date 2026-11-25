"""Shared helpers for the test suite."""

from __future__ import annotations

import copy
import csv
from pathlib import Path

from ate.config import AppConfig, _read_yaml, parse_config
from ate.core.transport import CommTimeout, ConnectionLost, Transport
from ate.sequence.runner import RunResult, TestRunner

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"

NO_FAULTS = {
    "timeout_rate": 0.0,
    "garbled_rate": 0.0,
    "invalid_rate": 0.0,
    "channel_garbled_rate": 0.0,
    "channel_invalid_rate": 0.0,
}


# --------------------------------------------------------------------------- configs + runs


def load_data(name: str = "default.yaml") -> dict:
    """A config file as a plain dict (``extends`` already resolved), ready to modify."""
    return _read_yaml(CONFIG_DIR / name)


def build_config(tmp_path: Path, data: dict | None = None, *, clean: bool = False, mode: str = "in_process",
                 seed: int = 1, **dotted) -> AppConfig:
    """Config for a test: results in tmp_path, in-process by default, optionally with all faults off.
    Extra keyword arguments override settings, with ``__`` for dots: sequence__measurement__read_mode="all"."""
    data = copy.deepcopy(data if data is not None else load_data())
    data.setdefault("run", {}).update(results_dir=str(tmp_path / "results"), seed=seed, save_plots=False)
    data.setdefault("simulation", {})["mode"] = mode
    if clean:
        for inst in data["instruments"]:
            inst.setdefault("simulation", {})["faults"] = dict(NO_FAULTS)
    return parse_config(data, overrides={k.replace("__", "."): v for k, v in dotted.items()} or None)


def network_config(tmp_path: Path, base_port: int, data: dict | None = None, **kwargs) -> AppConfig:
    return build_config(tmp_path, data, mode="network", simulation__control_port=base_port,
                        simulation__first_instrument_port=base_port + 1, **kwargs)


def run(config: AppConfig, **kwargs) -> RunResult:
    return TestRunner(config, console=False, **kwargs).run()


def run_with(tmp_path: Path, *actions, data: dict | None = None, **config_kwargs):
    """Run in-process with all random faults off. ``actions`` are applied to the bench first
    (e.g. force a fault). Returns (result, bench)."""
    captured = {}

    def hook(bench):
        captured["bench"] = bench
        for action in actions:
            action(bench)
    result = run(build_config(tmp_path, data, clean=True, **config_kwargs), bench_hook=hook)
    return result, captured["bench"]


# --------------------------------------------------------------------------- reading results


def raw_rows(result: RunResult) -> list[dict]:
    with open(result.run_dir / "raw_measurements.csv", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def summary_rows(result: RunResult) -> list[dict]:
    with open(result.run_dir / "summary.csv", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def rows_at(result: RunResult, index: int, phase: str = "measure") -> list[dict]:
    return [r for r in raw_rows(result) if r["phase"] == phase and r["measurement_index"] == str(index)]


def comparable(result: RunResult) -> list[dict]:
    """Raw rows without the run ID and real-time timestamp (the only things that differ between replays)."""
    return [{k: v for k, v in r.items() if k not in ("run_id", "timestamp_utc")} for r in raw_rows(result)]


def channel(result: RunResult, number: int, condition: int = 0):
    return next(c for c in result.summary.conditions[condition].channels if c.channel == number)


# --------------------------------------------------------------------------- fake instrument


TIMEOUT, LOST = object(), object()


class ScriptedTransport(Transport):
    """A fake instrument connection that replies from a list, in order:
    a string, TIMEOUT (no reply) or LOST (connection gone). Records what was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent: list[str] = []
        self.closed = False

    def query(self, command: str) -> str:
        self.sent.append(command)
        reply = self.replies.pop(0)
        if reply is TIMEOUT:
            raise CommTimeout(command)
        if reply is LOST:
            raise ConnectionLost(command)
        return reply

    def close(self) -> None:
        self.closed = True
