"""Writes every attempt at every reading to the raw CSV the moment it happens.

Each row is flushed immediately, so if the program crashes at reading 237,
the first 236 are already safely in the file.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ate.data.models import Measurement

RAW_COLUMNS = [
    "run_id",
    "device_serial",
    "timestamp_utc",
    "sim_elapsed_s",
    "condition",
    "phase",
    "instrument",
    "instrument_type",
    "channel",
    "instrument_input",
    "measurement_index",
    "settle_attempt",
    "attempt",
    "value",
    "unit",
    "status",
    "raw_response",
    "note",
]


def _fmt(value: object, digits: int | None = None) -> str:
    if value is None:
        return ""
    if digits is not None and isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


class RawRecorder:
    def __init__(self, path: Path, run_id: str, device_serial: str):
        self.path = path
        self.run_id = run_id
        self.device_serial = device_serial
        self.rows_written = 0
        self._file = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=RAW_COLUMNS)
        self._writer.writeheader()
        self._file.flush()

    def write(self, m: Measurement) -> None:
        self._writer.writerow(
            {
                "run_id": self.run_id,
                "device_serial": self.device_serial,
                "timestamp_utc": m.timestamp_utc,
                "sim_elapsed_s": _fmt(m.sim_elapsed_s, 3),
                "condition": m.condition,
                "phase": m.phase,
                "instrument": m.instrument,
                "instrument_type": m.instrument_type,
                "channel": _fmt(m.channel),
                "instrument_input": _fmt(m.instrument_input),
                "measurement_index": _fmt(m.measurement_index),
                "settle_attempt": _fmt(m.settle_attempt),
                "attempt": m.attempt,
                "value": _fmt(m.value, 4),
                "unit": m.unit,
                "status": m.status.value,
                "raw_response": _fmt(m.raw_response),
                "note": m.note,
            }
        )
        self._file.flush()
        self.rows_written += 1

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> RawRecorder:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
