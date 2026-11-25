"""Roles: what *any* instrument of a kind must be able to do, whatever its brand.

The test sequence only ever asks for "a temperature controller" or "a power meter".
A different brand of meter is a new folder under ate/instruments/ whose driver extends
``PowerMeter``. The sequence, recorder and analysis do not change.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from pydantic import Field, field_validator

from ate.core.instrument import Instrument, TransportFactory
from ate.core.settings import InstrumentConfigBase, check_range
from ate.data.models import Measurement, Status

ROLE_TEMPERATURE = "temperature_control"
ROLE_POWER_METER = "power_meter"


# --------------------------------------------------------------------------- temperature controller


class TemperatureControllerSettings(InstrumentConfigBase):
    range_c: tuple[float, float] = (-10.0, 100.0)  # outside this = physically impossible reading

    @field_validator("range_c")
    @classmethod
    def _range(cls, value: tuple[float, float]) -> tuple[float, float]:
        return check_range(value, "range_c")


class TemperatureController(Instrument):
    role = ROLE_TEMPERATURE
    UNIT = "C"
    config: TemperatureControllerSettings

    @abstractmethod
    def set_temperature(self, celsius: float) -> None: ...

    @abstractmethod
    def output(self, on: bool) -> None:
        """Switch heating/cooling on or off."""

    @abstractmethod
    def read_temperature(self) -> Measurement: ...

    def _safe_shutdown(self) -> None:
        # Never leave the device being heated or cooled after the test, whatever the brand.
        self.output(False)


# --------------------------------------------------------------------------- power meter


class PowerMeterSettings(InstrumentConfigBase):
    INPUTS: ClassVar[int] = 8  # each meter model sets its own

    device_channels: list[int] = Field(min_length=1)  # meter input n is wired to this device channel
    range_dbm: tuple[float, float] = (-60.0, 10.0)  # outside this = physically impossible reading

    @field_validator("range_dbm")
    @classmethod
    def _range(cls, value: tuple[float, float]) -> tuple[float, float]:
        return check_range(value, "range_dbm")

    @field_validator("device_channels")
    @classmethod
    def _channels(cls, value: list[int]) -> list[int]:
        if len(value) > cls.INPUTS:
            raise ValueError(f"lists {len(value)} channels but this meter only has {cls.INPUTS} inputs")
        if len(set(value)) != len(value):
            raise ValueError("the same device channel is listed twice")
        return value


@dataclass(frozen=True)
class ReadAllResult:
    """Result of reading every input at once.

    ``whole_failure`` is set when the *message* failed (no reply, or nothing readable at all).
    Otherwise each Measurement carries its own status, so one bad channel never spoils the others.
    """

    measurements: list[Measurement]
    whole_failure: Status | None = None


class PowerMeter(Instrument):
    role = ROLE_POWER_METER
    UNIT = "dBm"
    config: PowerMeterSettings

    def __init__(self, config: PowerMeterSettings, transport_factory: TransportFactory):
        super().__init__(config, transport_factory)
        # meter input number -> device channel it is wired to
        self.input_map: dict[int, int] = {n: ch for n, ch in enumerate(config.device_channels, start=1)}

    @property
    def inputs(self) -> list[int]:
        return list(self.input_map)

    @abstractmethod
    def read_input(self, number: int) -> Measurement:
        """Read one input."""

    @abstractmethod
    def read_all(self) -> ReadAllResult:
        """Read every wired input at the same instant."""

    def _measurement(self, number: int, status: Status, value: float | None, raw: str | None,
                     stamp: str, note: str = "") -> Measurement:
        return Measurement(
            instrument=self.name,
            instrument_type=self.type_name,
            unit=self.UNIT,
            status=status,
            value=value,
            raw_response=raw,
            channel=self.input_map[number],
            instrument_input=number,
            timestamp_utc=stamp,
            note=note,
        )
