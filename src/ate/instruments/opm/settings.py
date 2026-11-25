"""Meter settings in the config file (on top of the settings every power meter has)."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from ate.core.roles import PowerMeterSettings
from ate.core.settings import FaultRates, StrictModel


class OpmSimSettings(StrictModel):
    """How the simulated meter misbehaves. (Its noise comes from the virtual device settings.)"""

    faults: FaultRates = Field(
        default_factory=lambda: FaultRates(
            timeout_rate=0.005,
            garbled_rate=0.005,
            channel_garbled_rate=0.002,
            channel_invalid_rate=0.001,
        )
    )


class OpmSettings(PowerMeterSettings):
    INPUTS: ClassVar[int] = 8
    type: Literal["sim_opm_8ch"] = "sim_opm_8ch"
    simulation: OpmSimSettings = Field(default_factory=OpmSimSettings)
