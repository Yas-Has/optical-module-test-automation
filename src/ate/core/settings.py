"""Settings shared by every instrument type."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Unknown settings are rejected (this catches typos) instead of being silently ignored."""

    model_config = ConfigDict(extra="forbid")


def check_range(value: tuple[float, float], what: str) -> tuple[float, float]:
    low, high = value
    if low >= high:
        raise ValueError(f"{what} must be [low, high] with low < high (got {list(value)})")
    return value


class FaultRates(StrictModel):
    """Chance (0-1) of each kind of simulated fault on each measurement query."""

    timeout_rate: float = Field(0.0, ge=0, le=1)
    garbled_rate: float = Field(0.0, ge=0, le=1)
    invalid_rate: float = Field(0.0, ge=0, le=1)
    channel_garbled_rate: float = Field(0.0, ge=0, le=1)
    channel_invalid_rate: float = Field(0.0, ge=0, le=1)


class InstrumentConfigBase(StrictModel):
    """What every instrument has in the config. Each instrument type extends this in its own
    ``settings.py`` (see ate/instruments/<name>/settings.py)."""

    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    type: str
    address: str = "auto"
    timeout_ms: int = Field(1000, gt=0)
    max_comm_retries: int = Field(1, ge=0, le=5)
    retry_delay_ms: int = Field(100, ge=0)
