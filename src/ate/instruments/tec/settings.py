"""TEC settings in the config file (on top of the settings every temperature controller has)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ate.core.roles import TemperatureControllerSettings
from ate.core.settings import FaultRates, StrictModel


class TecSimSettings(StrictModel):
    """How the simulated TEC behaves."""

    time_constant_s: float = Field(8.0, ge=0)  # how quickly the temperature approaches the setpoint
    jitter_c: float = Field(0.02, ge=0)  # reading wobble once settled
    steady_state_error_c: float = 0.0  # >0.5 simulates a controller that never reaches the setpoint
    faults: FaultRates = Field(
        default_factory=lambda: FaultRates(timeout_rate=0.005, garbled_rate=0.005, invalid_rate=0.001)
    )


class TecSettings(TemperatureControllerSettings):
    type: Literal["sim_tec"] = "sim_tec"
    simulation: TecSimSettings = Field(default_factory=TecSimSettings)
