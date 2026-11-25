"""TEC driver: turns "set 40 °C" into commands, and replies into Measurements."""

from __future__ import annotations

from ate.core.instrument import classify_value
from ate.core.roles import TemperatureController
from ate.data.models import Measurement, utc_now_iso
from ate.instruments.tec.commands import TecCommands


class TecDriver(TemperatureController):
    def set_temperature(self, celsius: float) -> None:
        self.send_setup(f"{TecCommands.SET_TEMPERATURE} {celsius:.3f}")

    def output(self, on: bool) -> None:
        self.send_setup(f"{TecCommands.OUTPUT} {'ON' if on else 'OFF'}")

    def read_temperature(self) -> Measurement:
        raw = self._measure_query(TecCommands.READ_TEMPERATURE)
        status, value = classify_value(raw, self.config.range_c)
        return Measurement(
            instrument=self.name,
            instrument_type=self.type_name,
            unit=self.UNIT,
            status=status,
            value=value,
            raw_response=raw,
            timestamp_utc=utc_now_iso(),
        )
