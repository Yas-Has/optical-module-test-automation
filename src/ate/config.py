"""Configuration: every number the system uses lives here, and is checked before a run starts.

Config files are YAML. A file may start with ``extends: <other file>`` to reuse another
config and override only what differs (used by the example configs).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, SerializeAsAny, ValidationError, field_validator, model_validator

from ate.core.roles import PowerMeterSettings, TemperatureControllerSettings
from ate.core.settings import InstrumentConfigBase
from ate.core.settings import StrictModel as _Model


class ConfigError(Exception):
    """Configuration is missing or invalid. The message is written for humans."""


# --------------------------------------------------------------------------- simulation


class DeviceModelConfig(_Model):
    """The virtual device under test: shared by every simulated instrument."""

    channels: int = Field(8, ge=1)
    nominal_power_dbm: float = 0.0
    channel_offsets_db: list[float] | None = None
    temp_coefficient_db_per_c: float = -0.1
    reference_temp_c: float = 40.0
    noise_db_at_100ms: float = Field(0.05, ge=0)
    ambient_temp_c: float = 25.0

    @model_validator(mode="after")
    def _offsets_match_channels(self) -> DeviceModelConfig:
        if self.channel_offsets_db is not None and len(self.channel_offsets_db) != self.channels:
            raise ValueError(
                f"channel_offsets_db has {len(self.channel_offsets_db)} values but the device "
                f"has {self.channels} channels"
            )
        return self

    def offset_db(self, channel: int) -> float:
        if self.channel_offsets_db is None:
            return 0.0
        return self.channel_offsets_db[channel - 1]


class SimulationConfig(_Model):
    enabled: bool = True
    mode: Literal["network", "in_process"] = "network"
    time_scale: float = Field(0.0, ge=0, le=1)
    host: str = "127.0.0.1"
    control_port: int = Field(5100, ge=1, le=65535)
    first_instrument_port: int = Field(5101, ge=1, le=65535)
    device: DeviceModelConfig = Field(default_factory=DeviceModelConfig)


# Instrument settings live with each instrument (ate/instruments/<name>/settings.py), built on
# the shared bases in ate/core/settings.py and ate/core/roles.py.


# --------------------------------------------------------------------------- sequence + limits


class Condition(_Model):
    setpoint_c: float
    name: str | None = None
    tecs: list[str] | None = None  # None = every temperature controller

    @property
    def label(self) -> str:
        return self.name or f"{self.setpoint_c:g}C"


class TemperatureConfig(_Model):
    tolerance_c: float = Field(0.5, gt=0)
    poll_interval_s: float = Field(1.0, gt=0)
    stable_for_s: float = Field(10.0, ge=0)
    max_drift_c: float = Field(0.1, gt=0)  # max - min allowed across the stability window
    max_wait_s: float = Field(120.0, gt=0)
    max_settle_attempts: int = Field(2, ge=1)
    continue_on_fail: bool = True
    monitor_during_measurement: bool = True

    @model_validator(mode="after")
    def _stable_within_wait(self) -> TemperatureConfig:
        if self.stable_for_s > self.max_wait_s:
            raise ValueError(
                f"stable_for_s ({self.stable_for_s}) is longer than max_wait_s ({self.max_wait_s}), "
                "so the temperature could never pass"
            )
        return self


class MeasurementConfig(_Model):
    readings_per_channel: int = Field(50, ge=1)
    read_mode: Literal["all", "per_channel"] = "all"
    averaging_time_ms: float = Field(100.0, gt=0)
    interval_ms: float = Field(100.0, gt=0)

    @model_validator(mode="after")
    def _interval_not_shorter(self) -> MeasurementConfig:
        if self.interval_ms < self.averaging_time_ms:
            raise ValueError(
                f"interval_ms ({self.interval_ms:g}) is shorter than averaging_time_ms "
                f"({self.averaging_time_ms:g}): the meter would be asked for a new reading "
                "before the last one finished"
            )
        return self


class SequenceConfig(_Model):
    conditions: list[Condition] = Field(default_factory=lambda: [Condition(setpoint_c=40.0)], min_length=1)
    temperature: TemperatureConfig = Field(default_factory=TemperatureConfig)
    measurement: MeasurementConfig = Field(default_factory=MeasurementConfig)

    @field_validator("conditions")
    @classmethod
    def _unique_labels(cls, value: list[Condition]) -> list[Condition]:
        labels = [c.label for c in value]
        duplicates = sorted({x for x in labels if labels.count(x) > 1})
        if duplicates:
            raise ValueError(f"conditions must have unique names; repeated: {duplicates}")
        return value


class PowerLimits(_Model):
    min: float = -3.0
    max: float = 3.0

    @model_validator(mode="after")
    def _ordered(self) -> PowerLimits:
        if self.min >= self.max:
            raise ValueError(f"min ({self.min}) must be below max ({self.max})")
        return self


class LimitsConfig(_Model):
    avg_power_dbm: PowerLimits = Field(default_factory=PowerLimits)
    require_all_valid: bool = True
    max_bad_readings: int = Field(0, ge=0)  # with require_all_valid: readings per channel allowed to not end VALID
    retry_warning_fraction: float = Field(0.05, ge=0, le=1)
    temp_drift_warning: bool = True


class RunConfig(_Model):
    device_serial: str = Field("SN-0001", min_length=1)
    results_dir: str = "results"
    seed: int | None = None
    save_plots: bool = True  # charts at the end of each run (needs matplotlib; skipped if missing)


# --------------------------------------------------------------------------- top level


class AppConfig(_Model):
    run: RunConfig = Field(default_factory=RunConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    instruments: list[SerializeAsAny[InstrumentConfigBase]]
    sequence: SequenceConfig = Field(default_factory=SequenceConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    @field_validator("instruments", mode="before")
    @classmethod
    def _parse_instruments(cls, value: Any) -> list[InstrumentConfigBase]:
        """Each entry is validated against the settings model of its own instrument type."""
        from ate.core import registry

        if not isinstance(value, list) or not value:
            raise ValueError("at least one instrument must be listed")
        parsed: list[InstrumentConfigBase] = []
        for number, item in enumerate(value, start=1):
            if isinstance(item, InstrumentConfigBase):
                parsed.append(item)
                continue
            if not isinstance(item, dict):
                raise ValueError(f"instrument #{number} must be a mapping of settings")
            label = item.get("name", f"#{number}")
            entry = registry.find(item.get("type"))
            if entry is None:
                raise ValueError(
                    f"instrument '{label}' has unknown type '{item.get('type')}'. "
                    f"Known types: {', '.join(registry.known_types())}"
                )
            try:
                parsed.append(entry.config_model.model_validate(item))
            except ValidationError as exc:
                raise ValueError(f"instrument '{label}': " + "; ".join(_error_lines(exc))) from None
        return parsed

    @model_validator(mode="after")
    def _cross_checks(self) -> AppConfig:
        problems: list[str] = []
        names = [i.name for i in self.instruments]
        for name in sorted({n for n in names if names.count(n) > 1}):
            problems.append(f"two instruments are both named '{name}'")

        explicit = [i.address for i in self.instruments if i.address != "auto"]
        for address in sorted({a for a in explicit if explicit.count(a) > 1}):
            problems.append(f"two instruments share the address '{address}'")

        if not self.tecs():
            problems.append("no temperature controller is configured (a TEC is needed to set the temperature)")
        if not self.meters():
            problems.append("no power meter is configured")

        averaging = self.sequence.measurement.averaging_time_ms
        for meter in self.meters():
            if meter.timeout_ms <= averaging:
                problems.append(
                    f"{meter.name}: timeout_ms ({meter.timeout_ms}) must be longer than averaging_time_ms "
                    f"({averaging:g}), otherwise every reading would time out"
                )

        channels = self.simulation.device.channels
        owners: dict[int, list[str]] = {}
        for meter in self.meters():
            for channel in meter.device_channels:
                owners.setdefault(channel, []).append(meter.name)
        for channel in sorted(owners):
            if not 1 <= channel <= channels:
                problems.append(f"device channel {channel} does not exist (the device has {channels} channels)")
        for channel in range(1, channels + 1):
            meters = owners.get(channel, [])
            if not meters:
                problems.append(f"device channel {channel} is not connected to any power meter")
            elif len(meters) > 1:
                problems.append(f"device channel {channel} is connected to more than one meter ({', '.join(meters)})")

        if not self.simulation.enabled:
            for inst in self.instruments:
                if inst.address == "auto":
                    problems.append(f"{inst.name}: real hardware needs a real address, not 'auto'")

        readings = self.sequence.measurement.readings_per_channel
        if self.limits.require_all_valid and self.limits.max_bad_readings >= readings:
            problems.append(
                f"limits.max_bad_readings ({self.limits.max_bad_readings}) must be less than "
                f"readings_per_channel ({readings}); to accept any number, set require_all_valid: false"
            )

        tec_names = {t.name for t in self.tecs()}
        for cond in self.sequence.conditions:
            for name in cond.tecs or []:
                if name not in tec_names:
                    problems.append(f"condition {cond.label}: '{name}' is not a temperature controller")

        if problems:
            raise ValueError("\n".join(problems))
        return self

    # ----------------------------------------------------------------- helpers

    def tecs(self) -> list[TemperatureControllerSettings]:
        return [i for i in self.instruments if isinstance(i, TemperatureControllerSettings)]

    def meters(self) -> list[PowerMeterSettings]:
        return [i for i in self.instruments if isinstance(i, PowerMeterSettings)]

    def instrument(self, name: str) -> InstrumentConfigBase:
        for inst in self.instruments:
            if inst.name == name:
                return inst
        raise KeyError(name)

    def channel_map(self) -> dict[int, tuple[str, int]]:
        """Device channel -> (meter name, meter input number)."""
        mapping: dict[int, tuple[str, int]] = {}
        for meter in self.meters():
            for number, channel in enumerate(meter.device_channels, start=1):
                mapping[channel] = (meter.name, number)
        return dict(sorted(mapping.items()))

    def estimated_real_duration_s(self) -> float:
        """Rough real-world duration, used to warn before very long runs."""
        if self.simulation.enabled and self.simulation.time_scale == 0:
            return 0.0
        scale = self.simulation.time_scale if self.simulation.enabled else 1.0
        t, m = self.sequence.temperature, self.sequence.measurement
        per_round = m.interval_ms / 1000 * (1 if m.read_mode == "all" else self.simulation.device.channels)
        per_condition = t.stable_for_s + 30 + m.readings_per_channel * per_round
        return scale * per_condition * len(self.sequence.conditions)


# --------------------------------------------------------------------------- loading / saving


def _error_lines(exc: ValidationError) -> list[str]:
    lines = []
    for err in exc.errors():
        where = ".".join(str(part) for part in err["loc"])
        message = str(err["msg"]).removeprefix("Value error, ")
        for line in message.splitlines():
            lines.append(f"{where}: {line}" if where else line)
    return lines


def _deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _read_yaml(path: Path, chain: tuple[Path, ...] = ()) -> dict:
    path = path.resolve()
    if path in chain:
        raise ConfigError(f"config files extend each other in a loop: {path}")
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping of settings at the top level")
    parent = data.pop("extends", None)
    if parent:
        data = _deep_merge(_read_yaml(path.parent / parent, chain + (path,)), data)
    return data


def _apply_overrides(data: dict, overrides: dict[str, Any]) -> dict:
    data = copy.deepcopy(data)
    for dotted, value in overrides.items():
        node = data
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = value
    return data


def parse_config(data: dict, source: str = "config", overrides: dict[str, Any] | None = None) -> AppConfig:
    if overrides:
        data = _apply_overrides(data, overrides)
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        details = "\n".join(f"  - {line}" for line in _error_lines(exc))
        raise ConfigError(f"{source} has problems:\n{details}") from None


def load_config(path: str | Path, overrides: dict[str, Any] | None = None) -> AppConfig:
    """Load, merge (``extends``), override and validate a config file."""
    return parse_config(_read_yaml(Path(path)), source=str(path), overrides=overrides)


def config_to_dict(config: AppConfig) -> dict:
    return config.model_dump(mode="json")


def save_config(config: AppConfig, path: str | Path) -> None:
    Path(path).write_text(yaml.safe_dump(config_to_dict(config), sort_keys=False), encoding="utf-8")
