"""ate/simulation/device.py: the virtual device, and the device link between the instruments."""

from __future__ import annotations

import statistics

import pytest

from ate.config import DeviceModelConfig
from ate.core.settings import FaultRates
from ate.instruments.opm import OpmSettings, OpmSimulator
from ate.instruments.opm.settings import OpmSimSettings
from ate.instruments.tec import TecSettings, TecSimulator
from ate.instruments.tec.settings import TecSimSettings
from ate.simulation.clock import SimClock
from ate.simulation.device import VirtualDevice


class FixedHeat:
    def __init__(self, celsius):
        self.celsius = celsius

    def true_temperature(self):
        return self.celsius


def test_without_a_tec_the_device_sits_at_room_temperature():
    assert VirtualDevice(DeviceModelConfig(ambient_temp_c=22.0)).temperature() == 22.0


def test_power_model():
    device = VirtualDevice(DeviceModelConfig(channel_offsets_db=[0.1, -0.4, 0, 0, 0, 0, 0, 0],
                                             nominal_power_dbm=0.0, reference_temp_c=40.0,
                                             temp_coefficient_db_per_c=-0.1))
    device.attach_tec(FixedHeat(40.0))
    assert device.true_power_dbm(1) == pytest.approx(0.1)
    assert device.true_power_dbm(2) == pytest.approx(-0.4)
    device.attach_tec(FixedHeat(44.0))  # two TECs: device sits at their average, 42 C
    assert device.temperature() == 42.0
    assert device.true_power_dbm(1) == pytest.approx(0.1 - 0.2)


def test_device_link_tec_changes_what_the_meter_reads():
    """The two simulated instruments never talk to each other, only through the shared device."""
    clock = SimClock()
    device = VirtualDevice(DeviceModelConfig())
    tec = TecSimulator(TecSettings(name="tec", simulation=TecSimSettings(jitter_c=0, faults=FaultRates())),
                       device, clock, seed=1)
    meter = OpmSimulator(OpmSettings(name="opm", device_channels=list(range(1, 9)),
                                     simulation=OpmSimSettings(faults=FaultRates())), device, clock, seed=1)

    def average_power():
        return statistics.fmean(float(meter.handle("POW? 2").text) for _ in range(2000))

    tec.handle("OUTP ON")
    tec.handle("TEMP 40")
    clock.advance(200)
    at_40 = average_power()
    tec.handle("TEMP 30")
    clock.advance(200)
    at_30 = average_power()
    assert at_30 - at_40 == pytest.approx(1.0, abs=0.01)  # -0.1 dB/C x -10 C: cooler is brighter
