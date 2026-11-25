"""ate/core/roles.py: what any temperature controller / power meter must provide, whatever the brand."""

from __future__ import annotations

import pytest

from ate.core.roles import (
    ROLE_POWER_METER,
    ROLE_TEMPERATURE,
    PowerMeter,
    PowerMeterSettings,
    TemperatureController,
    TemperatureControllerSettings,
)
from ate.data.models import Status


def test_roles_cannot_be_used_without_implementing_them():
    with pytest.raises(TypeError):
        TemperatureController(None, None)  # abstract: set_temperature, output, read_temperature missing


class BareMeter(PowerMeter):
    """A minimal meter of some 'other brand', to test what the role itself provides."""

    def read_input(self, number):
        raise NotImplementedError

    def read_all(self):
        raise NotImplementedError


def test_power_meter_maps_inputs_to_device_channels():
    meter = BareMeter(PowerMeterSettings(name="m", type="bare", device_channels=[9, 10, 11]), lambda: None)
    assert meter.input_map == {1: 9, 2: 10, 3: 11} and meter.inputs == [1, 2, 3]
    m = meter._measurement(2, Status.VALID, -0.1, "2:-0.1", "t")
    assert (m.channel, m.instrument_input, m.unit, m.instrument) == (10, 2, "dBm", "m")


def test_role_names():
    assert TemperatureController.role == ROLE_TEMPERATURE and PowerMeter.role == ROLE_POWER_METER


def test_temperature_controller_switches_off_when_made_safe():
    calls = []

    class BareTec(TemperatureController):
        def set_temperature(self, celsius): ...
        def output(self, on): calls.append(on)
        def read_temperature(self): ...

    BareTec(TemperatureControllerSettings(name="t", type="bare"), lambda: None)._safe_shutdown()
    assert calls == [False]
