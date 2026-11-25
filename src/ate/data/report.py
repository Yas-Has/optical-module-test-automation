"""Summary CSV, run_info.json, the terminal result table, and re-processing saved runs."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from ate.config import AppConfig, load_config
from ate.data.analysis import RunSummary, analyze, load_raw_rows

RAW_FILE = "raw_measurements.csv"
SUMMARY_FILE = "summary.csv"
RUN_INFO_FILE = "run_info.json"
CONFIG_FILE = "config_used.yaml"
LOG_FILE = "run.log"

SUMMARY_COLUMNS = [
    "run_id",
    "device_serial",
    "condition",
    "setpoint_c",
    "temp_verification",
    "temp_settle_attempts",
    "temp_mean_c",
    "temp_min_during_meas_c",
    "temp_max_during_meas_c",
    "channel",
    "instrument",
    "instrument_input",
    "readings_expected",
    "valid_count",
    "invalid_count",
    "timeout_count",
    "garbled_count",
    "not_measured_count",
    "retries_used",
    "avg_dbm",
    "min_dbm",
    "max_dbm",
    "std_db",
    "averaging_time_ms",
    "interval_ms",
    "channel_result",
    "fail_reasons",
    "warnings",
    "overall_result",
]


def _num(value: float | None, digits: int = 4) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def write_summary_csv(path: Path, summary: RunSummary, config: AppConfig) -> None:
    measurement = config.sequence.measurement
    overall = summary.overall_result
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for cond in summary.conditions:
            attempts = max((t.settle_attempts for t in cond.tecs), default=0)
            for ch in cond.channels:
                writer.writerow({
                    "run_id": summary.run_id,
                    "device_serial": summary.device_serial,
                    "condition": cond.label,
                    "setpoint_c": f"{cond.setpoint_c:g}",
                    "temp_verification": cond.temp_result,
                    "temp_settle_attempts": attempts,
                    "temp_mean_c": _num(cond.temp_mean_c, 3),
                    "temp_min_during_meas_c": _num(cond.monitor_min_c, 3),
                    "temp_max_during_meas_c": _num(cond.monitor_max_c, 3),
                    "channel": ch.channel,
                    "instrument": ch.instrument,
                    "instrument_input": ch.instrument_input,
                    "readings_expected": ch.readings_expected,
                    "valid_count": ch.valid_count,
                    "invalid_count": ch.invalid_count,
                    "timeout_count": ch.timeout_count,
                    "garbled_count": ch.garbled_count,
                    "not_measured_count": ch.not_measured_count,
                    "retries_used": ch.retries_used,
                    "avg_dbm": _num(ch.avg_dbm),
                    "min_dbm": _num(ch.min_dbm),
                    "max_dbm": _num(ch.max_dbm),
                    "std_db": _num(ch.std_db),
                    "averaging_time_ms": f"{measurement.averaging_time_ms:g}",
                    "interval_ms": f"{measurement.interval_ms:g}",
                    "channel_result": ch.result,
                    "fail_reasons": ";".join(ch.fail_reasons),
                    "warnings": ";".join(ch.warnings),
                    "overall_result": overall,
                })


def write_json(path: Path, data: dict) -> None:
    """Write atomically, so a crash never leaves a half-written file."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def format_summary(summary: RunSummary) -> str:
    """Compact result table for the terminal and the log."""
    lines = [f"Device {summary.device_serial} | run {summary.run_id}"]
    for cond in summary.conditions:
        tec_text = ", ".join(
            f"{t.tec} {t.result}" + (f" {t.mean_c:.2f} C" if t.mean_c is not None else "")
            + (f" (attempt {t.settle_attempts})" if t.settle_attempts > 1 else "")
            for t in cond.tecs
        )
        lines.append("")
        lines.append(f"Condition {cond.label}: temperature {cond.temp_result} [{tec_text}]")
        if cond.warnings:
            lines.append(f"  warnings: {', '.join(cond.warnings)}")
        lines.append(f"  {'Ch':>3}  {'Meter:In':<10} {'Avg dBm':>8} {'Min':>8} {'Max':>8} {'Std':>7}"
                     f" {'Valid':>5} {'Inv':>4} {'T/O':>4} {'Garb':>4} {'N/M':>4} {'Retry':>5}  Result")
        for ch in cond.channels:
            lines.append(
                f"  {ch.channel:>3}  {f'{ch.instrument}:{ch.instrument_input}':<10}"
                f" {_num(ch.avg_dbm, 3):>8} {_num(ch.min_dbm, 3):>8} {_num(ch.max_dbm, 3):>8}"
                f" {_num(ch.std_db, 3):>7} {ch.valid_count:>5} {ch.invalid_count:>4} {ch.timeout_count:>4}"
                f" {ch.garbled_count:>4} {ch.not_measured_count:>4} {ch.retries_used:>5}  {ch.result}"
                + (f" ({', '.join(ch.fail_reasons)})" if ch.fail_reasons else "")
            )
    lines.append("")
    lines.append(f"OVERALL: {summary.overall_result}")
    return "\n".join(lines)


def reprocess_run(run_dir: Path, config_path: Path | None = None) -> tuple[RunSummary, Path]:
    """Re-run the analysis on saved raw data, without re-testing.

    With the run's own config, summary.csv is regenerated in place (it must come out identical).
    With a different config (e.g. new limits), the result goes to a new, timestamped file so the
    original summary is never overwritten.
    """
    run_dir = Path(run_dir)
    raw_path = run_dir / RAW_FILE
    if not raw_path.is_file():
        raise FileNotFoundError(f"no {RAW_FILE} in {run_dir}")
    config = load_config(config_path or run_dir / CONFIG_FILE)
    info_path = run_dir / RUN_INFO_FILE
    info = read_json(info_path) if info_path.is_file() else {}
    summary = analyze(
        load_raw_rows(raw_path), config,
        info.get("run_id", run_dir.name), info.get("device_serial", config.run.device_serial),
    )
    if config_path is None:
        out = run_dir / SUMMARY_FILE
    else:
        out = run_dir / f"summary_reprocessed_{datetime.now():%Y%m%d-%H%M%S}.csv"
    write_summary_csv(out, summary, config)
    return summary, out
