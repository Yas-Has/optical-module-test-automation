"""Meter driver: turns replies like '1:-0.12,2:ERR,...' into one Measurement per channel."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ate.core.instrument import classify_value
from ate.core.roles import PowerMeter, ReadAllResult
from ate.data.models import Measurement, Status, utc_now_iso
from ate.instruments.opm.commands import OpmCommands

if TYPE_CHECKING:
    from ate.config import AppConfig

_FIELD = re.compile(r"^\s*(\d+)\s*:(.*)$")


def parse_labelled_fields(reply: str) -> dict[int, tuple[str, str]]:
    """'1:-0.12,2:ERR' -> {1: ('1:-0.12', '-0.12'), 2: ('2:ERR', 'ERR')}. Unlabelled pieces are ignored."""
    fields: dict[int, tuple[str, str]] = {}
    for piece in reply.split(","):
        match = _FIELD.match(piece)
        if match:
            fields.setdefault(int(match.group(1)), (piece.strip(), match.group(2).strip()))
    return fields


class OpmDriver(PowerMeter):
    def configure(self, app_config: AppConfig) -> None:
        self.send_setup(f"{OpmCommands.UNIT} DBM")
        self.send_setup(f"{OpmCommands.AVERAGING} {app_config.sequence.measurement.averaging_time_ms:g}")

    def read_input(self, number: int) -> Measurement:
        """Read one input (per-channel mode, and re-reading one damaged channel)."""
        raw = self._measure_query(f"{OpmCommands.READ_ONE} {number}")
        status, value = classify_value(raw, self.config.range_dbm)
        return self._measurement(number, status, value, raw, utc_now_iso())

    def read_all(self) -> ReadAllResult:
        """Read every input at the same instant with one query.

        Each value is labelled with its input number, so a damaged or missing value only ever
        affects its own channel; the others are still unambiguously tied to the right channel.
        """
        raw = self._measure_query(OpmCommands.READ_ALL)
        stamp = utc_now_iso()  # one moment for all channels: they were measured together
        if raw is None:
            return ReadAllResult(
                [self._measurement(n, Status.TIMEOUT, None, None, stamp, "no reply") for n in self.inputs],
                Status.TIMEOUT,
            )
        fields = parse_labelled_fields(raw)
        if not fields:
            return ReadAllResult(
                [self._measurement(n, Status.GARBLED, None, raw, stamp, "reply unreadable") for n in self.inputs],
                Status.GARBLED,
            )
        measurements = []
        for number in self.inputs:
            if number not in fields:
                measurements.append(
                    self._measurement(number, Status.GARBLED, None, raw, stamp, "channel missing from reply")
                )
                continue
            field_text, value_text = fields[number]
            status, value = classify_value(value_text, self.config.range_dbm)
            measurements.append(self._measurement(number, status, value, field_text, stamp))
        return ReadAllResult(measurements)
