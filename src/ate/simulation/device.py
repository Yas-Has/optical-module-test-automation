"""The virtual device under test, shared by every simulated instrument.

This is where the physics lives, just like in real life: the TEC heats the device, the meter reads
its light, and the light depends on the device's temperature (the "device link").
"""

from __future__ import annotations

from statistics import fmean
from typing import Protocol

from ate.config import DeviceModelConfig


class HeatSource(Protocol):
    def true_temperature(self) -> float: ...


class VirtualDevice:
    def __init__(self, config: DeviceModelConfig):
        self.config = config
        self._heat_sources: list[HeatSource] = []

    def attach_tec(self, tec: HeatSource) -> None:
        self._heat_sources.append(tec)

    def temperature(self) -> float:
        """Device temperature: follows the TEC(s) it sits on, or the room if there are none."""
        if not self._heat_sources:
            return self.config.ambient_temp_c
        return fmean(source.true_temperature() for source in self._heat_sources)

    def true_power_dbm(self, channel: int) -> float:
        """Noise-free optical power of one channel at the current temperature.

        P = nominal + channel offset + coefficient x (T - reference)
        """
        cfg = self.config
        return (
            cfg.nominal_power_dbm
            + cfg.offset_db(channel)
            + cfg.temp_coefficient_db_per_c * (self.temperature() - cfg.reference_temp_c)
        )
