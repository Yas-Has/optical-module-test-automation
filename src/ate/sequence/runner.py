"""The test recipe.

    1. initialise instruments        5. process the data
    2. set the temperature           6. decide pass/fail
    3. verify the temperature        7. save raw data + summary
    4. take the optical readings     8. close instruments (always)

The runner only talks to drivers (through their common interface) and the recorder.
It never knows whether the instruments are real or simulated.
"""

from __future__ import annotations

import logging
import platform
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path

import ate
from ate.config import AppConfig, Condition, save_config
from ate.data.analysis import RunSummary, analyze, load_raw_rows
from ate.data.models import Measurement, Status, utc_now_iso
from ate.data.recorder import RawRecorder
from ate.data.report import (
    CONFIG_FILE,
    LOG_FILE,
    RAW_FILE,
    RUN_INFO_FILE,
    SUMMARY_FILE,
    format_summary,
    write_json,
    write_summary_csv,
)
from ate.core.instrument import Instrument, InstrumentError
from ate.core.roles import PowerMeter, TemperatureController
from ate.core.transport import ConnectionLost
from ate.logging_setup import start_run_logging, stop_run_logging
from ate.sequence.clock import RunClock
from ate.sequence.session import BenchHook, InstrumentSession
from ate.sequence.stability import StabilityTracker
from ate.simulation.control import BenchError, ControlClient, check_ports_free

log = logging.getLogger("ate.runner")

COMPLETE = "COMPLETE"
COMPLETE_WITH_ERRORS = "COMPLETE_WITH_ERRORS"
ABORTED = "ABORTED"

_UNSET = object()


class AbortRun(Exception):
    """The run cannot usefully continue (e.g. every power meter is gone)."""


@dataclass(frozen=True)
class RunResult:
    run_id: str
    run_dir: Path
    seed: int
    status: str
    abort_reason: str | None
    summary: RunSummary

    @property
    def overall_result(self) -> str:
        return self.summary.overall_result


def make_run_id(serial: str) -> str:
    safe_serial = re.sub(r"[^A-Za-z0-9_-]", "-", serial)
    return f"{datetime.now():%Y%m%d-%H%M%S}_{safe_serial}_{secrets.token_hex(2)}"


class TestRunner:
    __test__ = False  # not a pytest test class

    def __init__(self, config: AppConfig, *, external_bench: bool = False,
                 bench_hook: BenchHook | None = None, console: bool = True):
        self.config = config
        self.external_bench = external_bench
        self.bench_hook = bench_hook
        self.console = console
        self._session: InstrumentSession | None = None
        self._clock: RunClock | None = None
        self._recorder: RawRecorder | None = None
        self._attempted: set[tuple[str, int, int]] = set()
        self._dead: dict[str, str] = {}  # meter name -> reason
        self._skipped: dict[str, str] = {}  # condition label -> reason

    # ================================================================= the run

    def run(self) -> RunResult:
        seed = self._preflight()
        config = self.config.model_copy(update={"run": self.config.run.model_copy(update={"seed": seed})})
        self.config = config
        serial = config.run.device_serial
        run_id = make_run_id(serial)
        run_dir = Path(config.run.results_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=False)  # never overwrite an earlier run
        config_path = run_dir / CONFIG_FILE
        save_config(config, config_path)

        handlers = start_run_logging(run_dir / LOG_FILE, self.console)
        info: dict = {
            "run_id": run_id,
            "device_serial": serial,
            "seed": seed,
            "status": "RUNNING",
            "started_utc": utc_now_iso(),
            "mode": self._mode_text(),
            "software_version": ate.__version__,
            "python_version": platform.python_version(),
        }
        write_json(run_dir / RUN_INFO_FILE, info)
        status, abort_reason = COMPLETE, None
        try:
            log.info("Run %s | device %s | seed %d | %s", run_id, serial, seed, self._mode_text())
            log.info("Results folder: %s", run_dir)
            estimate = config.estimated_real_duration_s()
            if estimate > 300:
                log.warning("This run will take about %.0f minutes in real time", estimate / 60)

            with RawRecorder(run_dir / RAW_FILE, run_id, serial) as recorder:
                self._recorder = recorder
                try:
                    with InstrumentSession(config, seed=seed, config_path=config_path, run_dir=run_dir,
                                           external_bench=self.external_bench,
                                           bench_hook=self.bench_hook) as session:
                        self._session = session
                        self._clock = session.clock
                        info["instruments"] = session.connect_all()  # step 1
                        write_json(run_dir / RUN_INFO_FILE, info)
                        for condition in config.sequence.conditions:
                            self._run_condition(condition)  # steps 2-4
                    # step 8 happened as the `with` block closed: instruments off and disconnected
                except KeyboardInterrupt:
                    status, abort_reason = ABORTED, "stopped by the operator (Ctrl+C)"
                except AbortRun as exc:
                    status, abort_reason = ABORTED, str(exc)
                except (ConnectionLost, InstrumentError, BenchError) as exc:
                    status, abort_reason = ABORTED, str(exc)
                except Exception as exc:  # a bug must not lose the data collected so far
                    log.exception("unexpected error")
                    status, abort_reason = ABORTED, f"unexpected error: {exc!r}"
                if abort_reason:
                    log.error("RUN ABORTED: %s", abort_reason)
                self._clock = None
                self._fill_not_measured(abort_reason)

            # steps 5-7: always from the raw file, the single source of truth
            summary = analyze(load_raw_rows(run_dir / RAW_FILE), config, run_id, serial)
            write_summary_csv(run_dir / SUMMARY_FILE, summary, config)
            if status == COMPLETE and self._dead:
                status = COMPLETE_WITH_ERRORS
            info.update({
                "status": status,
                "abort_reason": abort_reason,
                "unreachable_instruments": self._dead,
                "finished_utc": utc_now_iso(),
                "overall_result": summary.overall_result,
                "temperature": [
                    {"condition": c.label, "setpoint_c": c.setpoint_c, "result": c.temp_result,
                     "tecs": [vars(t) for t in c.tecs]}
                    for c in summary.conditions
                ],
                "raw_rows": recorder.rows_written,
            })
            write_json(run_dir / RUN_INFO_FILE, info)
            log.info("\n%s", format_summary(summary))
            log.info("Run status: %s | results in %s", status, run_dir)
        finally:
            stop_run_logging(handlers)
        return RunResult(run_id, run_dir, seed, status, abort_reason, summary)

    # ================================================================= pre-flight

    def _preflight(self) -> int:
        """Checks that need no results folder. Returns the seed to use."""
        sim = self.config.simulation
        if sim.enabled and sim.mode == "network":
            if self.external_bench:
                client = ControlClient(sim.host, sim.control_port)
                try:
                    return int(client.send("SIM:SEED?"))  # the bench's seed is the one that matters
                finally:
                    client.close()
            check_ports_free(self.config)
        if self.config.run.seed is not None:
            return self.config.run.seed
        return secrets.randbelow(2**31)

    def _mode_text(self) -> str:
        sim = self.config.simulation
        if not sim.enabled:
            return "real hardware"
        return f"simulated ({sim.mode}{', external bench' if self.external_bench else ''}, time_scale {sim.time_scale:g})"

    # ================================================================= recording + retries

    def _record(self, m: Measurement, elapsed: float | None | object = _UNSET, **context) -> Measurement:
        if elapsed is _UNSET:
            elapsed = self._clock.elapsed() if self._clock else None
        m = m.with_context(sim_elapsed_s=elapsed, **context)
        self._recorder.write(m)
        if m.phase == "measure" and m.channel is not None and m.measurement_index is not None:
            self._attempted.add((m.condition, m.channel, m.measurement_index))
        return m

    def _account_timeout(self, instrument: Instrument, m: Measurement) -> None:
        if m.status is Status.TIMEOUT:
            self._clock.account(instrument.config.timeout_ms / 1000)

    def _pause_before_retry(self, instrument: Instrument) -> None:
        instrument.clear()  # drop anything left over, so a late reply can't be mistaken for the next one
        self._clock.wait(instrument.config.retry_delay_ms / 1000)

    def _read_with_retry(self, instrument: Instrument, read, **context) -> Measurement:
        """One reading. Communication problems (TIMEOUT/GARBLED) are retried; every attempt is recorded.
        INVALID and VALID results are final: an impossible value is evidence, not noise."""
        retries = instrument.config.max_comm_retries
        attempt = 0
        while True:
            attempt += 1
            m = read()
            self._account_timeout(instrument, m)
            m = self._record(m, attempt=attempt, **context)
            if not m.status.retryable or attempt > retries:
                return m
            log.info("%s: %s on %s reading %s, retrying", instrument.name, m.status.value,
                     context.get("phase"), context.get("measurement_index"))
            self._pause_before_retry(instrument)

    def _read_all_with_retry(self, meter: PowerMeter, cond: Condition, index: int) -> None:
        """One round of all channels on one meter.

        * whole message lost/unreadable -> re-ask for all channels
        * one channel damaged           -> re-read only that channel
        * one channel impossible value  -> recorded as INVALID, no retry
        """
        retries = meter.config.max_comm_retries
        context = {"condition": cond.label, "phase": "measure", "measurement_index": index}
        attempt = 0
        while True:
            attempt += 1
            result = meter.read_all()
            if result.whole_failure is Status.TIMEOUT:
                self._clock.account(meter.config.timeout_ms / 1000)
            elapsed = self._clock.elapsed()  # one moment for the whole reply
            for m in result.measurements:
                self._record(m, elapsed=elapsed, attempt=attempt, **context)
            if result.whole_failure is None or attempt > retries:
                break
            log.info("%s: whole reply %s on reading %d, retrying all channels",
                     meter.name, result.whole_failure.value, index)
            self._pause_before_retry(meter)

        for first in result.measurements:
            current, tries = first, attempt
            while current.status.retryable and tries <= retries:
                log.info("%s: channel %d %s on reading %d, re-reading that channel only",
                         meter.name, first.channel, current.status.value, index)
                self._pause_before_retry(meter)
                tries += 1
                current = meter.read_input(first.instrument_input)
                self._account_timeout(meter, current)
                current = self._record(current, attempt=tries, note=current.note or "re-read of single channel",
                                       **context)

    # ================================================================= steps 2-4

    def _tecs_for(self, cond: Condition) -> list[TemperatureController]:
        names = cond.tecs or [t.name for t in self._session.tecs]
        return [t for t in self._session.tecs if t.name in names]

    def _live_meters(self) -> list[PowerMeter]:
        return [m for m in self._session.meters if m.name not in self._dead]

    def _run_condition(self, cond: Condition) -> None:
        log.info("=== Condition %s: setpoint %.2f C ===", cond.label, cond.setpoint_c)
        if not self._settle(cond, self._tecs_for(cond)):
            if not self.config.sequence.temperature.continue_on_fail:
                reason = "temperature verification failed (continue_on_fail is off)"
                self._skipped[cond.label] = reason
                log.warning("%s: skipping optical measurements for %s", reason, cond.label)
                return
            log.warning("Temperature verification FAILED for %s. Measuring anyway for troubleshooting; "
                        "the device cannot pass.", cond.label)
        self._measure(cond)

    def _settle(self, cond: Condition, tecs: list[TemperatureController]) -> bool:
        """Steps 2-3: set the temperature, wait until it is stable, verify. One extra attempt if it isn't."""
        t = self.config.sequence.temperature
        pending = list(tecs)
        for attempt in range(1, t.max_settle_attempts + 1):
            for tec in pending:
                tec.set_temperature(cond.setpoint_c)
                tec.output(True)
            trackers = {tec.name: StabilityTracker(cond.setpoint_c, t.tolerance_c, t.poll_interval_s,
                                                   t.stable_for_s, t.max_drift_c)
                        for tec in pending}
            waited, index = 0.0, 0
            while pending:
                index += 1
                for tec in list(pending):
                    m = self._read_with_retry(tec, tec.read_temperature, condition=cond.label, phase="settle",
                                              settle_attempt=attempt, measurement_index=index)
                    tracker = trackers[tec.name]
                    if tracker.add(m.status, m.value):
                        pending.remove(tec)
                        log.info("%s: temperature verified, %.3f C (stable for %g s; settle attempt %d, %.0f s)",
                                 tec.name, tracker.window_mean, t.stable_for_s, attempt, waited)
                if not pending or waited + t.poll_interval_s > t.max_wait_s + 1e-9:
                    break
                self._clock.wait(t.poll_interval_s)
                waited += t.poll_interval_s
            if not pending:
                return True
            names = ", ".join(tec.name for tec in pending)
            if attempt < t.max_settle_attempts:
                log.warning("%s: not stable within %.0f s; trying again (settle attempt %d of %d)",
                            names, t.max_wait_s, attempt + 1, t.max_settle_attempts)
        log.error("%s: temperature verification FAILED after %d settle attempts",
                  ", ".join(tec.name for tec in pending), t.max_settle_attempts)
        return False

    def _measure(self, cond: Condition) -> None:
        """Step 4: N readings of every device channel, with background temperature monitoring."""
        m = self.config.sequence.measurement
        rest = max(0.0, (m.interval_ms - m.averaging_time_ms) / 1000)
        channels = len(self.config.channel_map())
        log.info("Measuring %d readings x %d channels (%s mode, %g ms averaging)",
                 m.readings_per_channel, channels, m.read_mode, m.averaging_time_ms)
        if m.read_mode == "all":
            for index in range(1, m.readings_per_channel + 1):
                for meter in self._live_meters():
                    try:
                        self._read_all_with_retry(meter, cond, index)
                    except ConnectionLost as exc:
                        self._meter_lost(meter, exc)
                self._monitor(cond, index)
                self._clock.wait(rest)
            return

        count = 0
        for meter in self._live_meters():
            for number in meter.inputs:
                for index in range(1, m.readings_per_channel + 1):
                    try:
                        self._read_with_retry(meter, partial(meter.read_input, number), condition=cond.label,
                                              phase="measure", measurement_index=index)
                    except ConnectionLost as exc:
                        self._meter_lost(meter, exc)
                        break
                    count += 1
                    if count % channels == 0:
                        self._monitor(cond, count // channels)
                    self._clock.wait(rest)
                if meter.name in self._dead:
                    break

    def _monitor(self, cond: Condition, index: int) -> None:
        if not self.config.sequence.temperature.monitor_during_measurement:
            return
        for tec in self._tecs_for(cond):
            self._read_with_retry(tec, tec.read_temperature, condition=cond.label, phase="monitor",
                                  measurement_index=index)

    def _meter_lost(self, meter: PowerMeter, exc: Exception) -> None:
        reason = f"{meter.name} unreachable: {exc}"
        self._dead[meter.name] = reason
        log.error("%s. Its remaining readings will be recorded as NOT_MEASURED; other meters carry on.", reason)
        if not self._live_meters():
            raise AbortRun("every power meter is unreachable")

    # ================================================================= nothing silently missing

    def _fill_not_measured(self, abort_reason: str | None) -> None:
        """Every planned reading that was never attempted gets a NOT_MEASURED row with the reason."""
        config = self.config
        types = {i.name: i.type for i in config.instruments}
        missing = 0
        for cond in config.sequence.conditions:
            for channel, (meter, number) in config.channel_map().items():
                reason = (self._dead.get(meter) or self._skipped.get(cond.label)
                          or (f"run aborted: {abort_reason}" if abort_reason else "not attempted"))
                for index in range(1, config.sequence.measurement.readings_per_channel + 1):
                    if (cond.label, channel, index) in self._attempted:
                        continue
                    self._recorder.write(Measurement(
                        instrument=meter, instrument_type=types[meter], unit=PowerMeter.UNIT,
                        status=Status.NOT_MEASURED, channel=channel, instrument_input=number,
                        condition=cond.label, phase="measure", measurement_index=index, note=reason,
                    ))
                    missing += 1
        if missing:
            log.warning("%d planned readings were not measured (recorded as NOT_MEASURED)", missing)
