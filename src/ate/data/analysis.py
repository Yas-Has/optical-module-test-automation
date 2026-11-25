"""Turns the raw CSV into statistics and pass/fail decisions.

Analysis *always* reads the raw file (during a live run and when re-processing an old one),
so the summary can never disagree with the raw data.
"""

from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ate.config import AppConfig, Condition
from ate.data.models import Status
from ate.sequence.stability import StabilityTracker

PASS, FAIL, NOT_RUN = "PASS", "FAIL", "NOT_RUN"
LIMIT_RESOLUTION_DIGITS = 6  # limits are checked to 0.000001 dB


# --------------------------------------------------------------------------- unit helpers


def dbm_to_mw(dbm: float) -> float:
    return 10 ** (dbm / 10)


def mw_to_dbm(mw: float) -> float:
    return 10 * math.log10(mw)


def power_average_dbm(values_dbm: list[float]) -> float:
    """Physically correct average: convert to milliwatts, average, convert back.

    dBm is logarithmic, so a plain average of dBm values slightly under-states the power.
    For variations of +/-0.1 dB the difference is under 0.001 dB, but this is the right method.
    """
    return mw_to_dbm(statistics.fmean(dbm_to_mw(v) for v in values_dbm))


# --------------------------------------------------------------------------- raw rows


@dataclass(frozen=True)
class RawRow:
    condition: str
    phase: str
    instrument: str
    channel: int | None
    instrument_input: int | None
    measurement_index: int | None
    settle_attempt: int | None
    attempt: int
    value: float | None
    status: Status
    elapsed_s: float | None = None  # time since the run started (used by the charts)


def _int(text: str) -> int | None:
    return int(text) if text not in ("", None) else None


def _float(text: str) -> float | None:
    return float(text) if text not in ("", None) else None


def load_raw_rows(path: Path) -> list[RawRow]:
    with open(path, newline="", encoding="utf-8") as handle:
        return [
            RawRow(
                condition=row["condition"],
                phase=row["phase"],
                instrument=row["instrument"],
                channel=_int(row["channel"]),
                instrument_input=_int(row["instrument_input"]),
                measurement_index=_int(row["measurement_index"]),
                settle_attempt=_int(row["settle_attempt"]),
                attempt=int(row["attempt"]),
                value=_float(row["value"]),
                status=Status(row["status"]),
                elapsed_s=_float(row.get("sim_elapsed_s", "")),
            )
            for row in csv.DictReader(handle)
        ]


def final_attempts(rows: list[RawRow]) -> list[RawRow]:
    """For each reading keep only its last attempt (the one that counts), in file order."""
    best: dict[tuple, RawRow] = {}
    for row in rows:
        key = (row.condition, row.phase, row.instrument, row.channel, row.settle_attempt, row.measurement_index)
        if key not in best or row.attempt >= best[key].attempt:
            best[key] = row
    return list(best.values())


# --------------------------------------------------------------------------- results


@dataclass
class TecVerification:
    tec: str
    result: str  # PASS / FAIL / NOT_RUN
    settle_attempts: int
    mean_c: float | None


@dataclass
class ChannelResult:
    channel: int
    instrument: str
    instrument_input: int
    readings_expected: int
    valid_count: int = 0
    invalid_count: int = 0
    timeout_count: int = 0
    garbled_count: int = 0
    not_measured_count: int = 0
    retries_used: int = 0
    avg_dbm: float | None = None
    min_dbm: float | None = None
    max_dbm: float | None = None
    std_db: float | None = None
    result: str = FAIL
    fail_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ConditionResult:
    label: str
    setpoint_c: float
    temp_result: str
    tecs: list[TecVerification]
    temp_mean_c: float | None
    monitor_min_c: float | None
    monitor_max_c: float | None
    channels: list[ChannelResult]
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.temp_result == PASS and all(c.result == PASS for c in self.channels)


@dataclass
class RunSummary:
    run_id: str
    device_serial: str
    conditions: list[ConditionResult]

    @property
    def overall_result(self) -> str:
        return PASS if self.conditions and all(c.passed for c in self.conditions) else FAIL


# --------------------------------------------------------------------------- analysis


def _verify_temperature(rows: list[RawRow], cond: Condition, tec: str, config: AppConfig) -> TecVerification:
    t = config.sequence.temperature
    settle = sorted(
        (r for r in rows if r.condition == cond.label and r.phase == "settle" and r.instrument == tec),
        key=lambda r: (r.settle_attempt or 0, r.measurement_index or 0),
    )
    if not settle:
        return TecVerification(tec, NOT_RUN, 0, None)
    attempts = sorted({r.settle_attempt or 1 for r in settle})
    for attempt in attempts:
        tracker = StabilityTracker(cond.setpoint_c, t.tolerance_c, t.poll_interval_s, t.stable_for_s,
                                   t.max_drift_c)
        for row in (r for r in settle if (r.settle_attempt or 1) == attempt):
            if tracker.add(row.status, row.value):
                return TecVerification(tec, PASS, attempt, tracker.window_mean)
    return TecVerification(tec, FAIL, attempts[-1], None)


def _channel_result(rows: list[RawRow], all_rows: list[RawRow], cond: Condition, channel: int,
                    meter: str, meter_input: int, config: AppConfig) -> ChannelResult:
    expected = config.sequence.measurement.readings_per_channel
    limits = config.limits
    result = ChannelResult(channel, meter, meter_input, expected)

    finals = [r for r in rows if r.condition == cond.label and r.phase == "measure" and r.channel == channel]
    counts = {status: 0 for status in Status}
    for row in finals:
        counts[row.status] += 1
    # Planned readings with no row at all (should not happen: the runner records NOT_MEASURED) still count.
    missing = expected - len({r.measurement_index for r in finals})
    result.valid_count = counts[Status.VALID]
    result.invalid_count = counts[Status.INVALID]
    result.timeout_count = counts[Status.TIMEOUT]
    result.garbled_count = counts[Status.GARBLED]
    result.not_measured_count = counts[Status.NOT_MEASURED] + max(0, missing)
    result.retries_used = sum(
        1 for r in all_rows
        if r.condition == cond.label and r.phase == "measure" and r.channel == channel and r.attempt > 1
    )

    values = [r.value for r in finals if r.status is Status.VALID and r.value is not None]
    if values:
        result.avg_dbm = power_average_dbm(values)
        result.min_dbm = min(values)
        result.max_dbm = max(values)
        result.std_db = statistics.stdev(values) if len(values) > 1 else 0.0

    if not values:
        result.fail_reasons.append("no_valid_readings")
    elif limits.require_all_valid and expected - result.valid_count > limits.max_bad_readings:
        result.fail_reasons.append(f"invalid_readings({expected - result.valid_count}/{expected})")
    if result.avg_dbm is not None:
        # Compare at a fixed resolution so the limits are truly inclusive: converting -3 dBm to mW
        # and back gives -3.0000000000000004, which must not count as "below -3".
        avg = round(result.avg_dbm, LIMIT_RESOLUTION_DIGITS)
        if avg < limits.avg_power_dbm.min:
            result.fail_reasons.append("avg_below_min")
        elif avg > limits.avg_power_dbm.max:
            result.fail_reasons.append("avg_above_max")
    result.result = PASS if not result.fail_reasons else FAIL

    if result.retries_used > limits.retry_warning_fraction * expected:
        result.warnings.append(f"high_retry_rate({result.retries_used})")
    return result


def analyze(rows: list[RawRow], config: AppConfig, run_id: str, device_serial: str) -> RunSummary:
    finals = final_attempts(rows)
    t = config.sequence.temperature
    channel_map = config.channel_map()
    all_tecs = [tec.name for tec in config.tecs()]
    conditions: list[ConditionResult] = []

    for cond in config.sequence.conditions:
        tec_names = cond.tecs or all_tecs
        tecs = [_verify_temperature(finals, cond, name, config) for name in tec_names]
        temp_result = PASS if all(v.result == PASS for v in tecs) else (
            NOT_RUN if all(v.result == NOT_RUN for v in tecs) else FAIL)
        means = [v.mean_c for v in tecs if v.mean_c is not None]

        monitor = [r.value for r in finals
                   if r.condition == cond.label and r.phase == "monitor" and r.instrument in tec_names
                   and r.status is Status.VALID and r.value is not None]
        warnings = []
        if config.limits.temp_drift_warning and any(abs(v - cond.setpoint_c) > t.tolerance_c for v in monitor):
            warnings.append("temp_drift_during_measurement")

        channels = [
            _channel_result(finals, rows, cond, channel, meter, meter_input, config)
            for channel, (meter, meter_input) in channel_map.items()
        ]
        for channel in channels:
            channel.warnings.extend(warnings)

        conditions.append(ConditionResult(
            label=cond.label,
            setpoint_c=cond.setpoint_c,
            temp_result=temp_result,
            tecs=tecs,
            temp_mean_c=statistics.fmean(means) if means else None,
            monitor_min_c=min(monitor) if monitor else None,
            monitor_max_c=max(monitor) if monitor else None,
            channels=channels,
            warnings=warnings,
        ))
    return RunSummary(run_id, device_serial, conditions)
