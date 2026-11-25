"""Finds every instrument automatically.

Each folder under ``ate/instruments/`` describes itself in its ``__init__.py``:

    INSTRUMENT_TYPES = [InstrumentType(name="sim_tec", config_model=..., driver=..., simulator=...)]

Adding an instrument = adding a folder. Nothing in this file (or anywhere else) needs to change.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ate.config import ConfigError
from ate.core.instrument import Instrument
from ate.core.settings import InstrumentConfigBase


@dataclass(frozen=True)
class InstrumentType:
    name: str  # what goes after `type:` in the config
    config_model: type[InstrumentConfigBase]
    driver: type[Instrument]
    simulator: Callable[..., Any] | None = None  # (config, device, clock, seed) -> simulator

    @property
    def role(self) -> str:
        return self.driver.role


_TYPES: dict[str, InstrumentType] = {}
_discovered = False


def register(instrument_type: InstrumentType) -> None:
    existing = _TYPES.get(instrument_type.name)
    if existing is not None and existing is not instrument_type:
        raise ValueError(f"two instruments both call themselves '{instrument_type.name}'")
    _TYPES[instrument_type.name] = instrument_type


def discover() -> None:
    """Import every instrument folder once and register what it declares."""
    global _discovered
    if _discovered:
        return
    import ate.instruments as package

    for module in pkgutil.iter_modules(package.__path__):
        if module.ispkg:
            instrument = importlib.import_module(f"{package.__name__}.{module.name}")
            for instrument_type in getattr(instrument, "INSTRUMENT_TYPES", ()):
                register(instrument_type)
    _discovered = True


def find(name: Any) -> InstrumentType | None:
    discover()
    return _TYPES.get(name) if isinstance(name, str) else None


def get(name: str) -> InstrumentType:
    entry = find(name)
    if entry is None:
        raise ConfigError(f"unknown instrument type '{name}'. Known types: {', '.join(known_types())}")
    return entry


def known_types() -> list[str]:
    discover()
    return sorted(_TYPES)
