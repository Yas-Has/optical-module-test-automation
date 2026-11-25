"""ate/instruments/opm/simulator.py: the simulated meter follows the measurement model in the design."""

from __future__ import annotations

import statistics

import pytest

from ate.config import DeviceModelConfig
from ate.core.settings import FaultRates
from ate.instruments.opm import OpmSettings, OpmSimulator
from ate.instruments.opm.settings import OpmSimSettings
from ate.simulation.clock import SimClock
from ate.simulation.device import VirtualDevice

OFFSETS = [0.10, -0.25, 0.05, -0.40, 0.15, -0.10, 0.20, -0.05]


def make_meter(seed=1, faults=None, device_channels=None, reference_temp_c=25.0):
    """A meter on a device with no TEC: the device sits at room temperature (25 C).
    Reference temperature 25 C by default, so readings are simply nominal + offset."""
    clock = SimClock()
    device = VirtualDevice(DeviceModelConfig(channel_offsets_db=OFFSETS, reference_temp_c=reference_temp_c))
    settings = OpmSettings(name="opm", device_channels=device_channels or list(range(1, 9)),
                           simulation=OpmSimSettings(faults=faults or FaultRates()))
    return clock, OpmSimulator(settings, device, clock, seed)


def read(meter, command="POW? 1") -> float:
    return float(meter.handle(command).text)


def test_noise_and_mean_match_configuration():
    _, meter = make_meter()
    values = [read(meter) for _ in range(4000)]
    assert statistics.fmean(values) == pytest.approx(0.10, abs=0.005)  # nominal + channel 1 offset
    assert statistics.stdev(values) == pytest.approx(0.05, rel=0.05)


def test_longer_averaging_means_less_noise():
    _, meter = make_meter()
    assert meter.handle("AVG 400").text == "OK" and meter.handle("AVG?").text == "400"
    values = [read(meter) for _ in range(4000)]
    assert statistics.stdev(values) == pytest.approx(0.025, rel=0.05)  # 4x longer -> half the noise


def test_reading_takes_the_averaging_time():
    clock, meter = make_meter()
    meter.handle("AVG 250")
    meter.handle("POW:ALL?")
    meter.handle("POW? 1")
    assert clock.now == pytest.approx(0.5)


def test_temperature_shifts_power():
    _, at_reference = make_meter(reference_temp_c=25.0)
    _, hotter = make_meter(reference_temp_c=15.0)  # device is 10 C above its reference
    assert read(hotter, "POW? 2") - read(at_reference, "POW? 2") == pytest.approx(-1.0, abs=1e-9)


def test_read_all_reports_every_input_labelled():
    _, meter = make_meter()
    fields = meter.handle("POW:ALL?").text.split(",")
    assert [f.split(":")[0] for f in fields] == [str(n) for n in range(1, 9)]


def test_unwired_input_reads_dark():
    _, meter = make_meter(device_channels=[1])
    assert read(meter, "POW? 5") == pytest.approx(-75.0, abs=0.5)


def test_forced_channel_fault_hits_only_that_channel():
    _, meter = make_meter()
    meter.faults.force("channel_invalid", 3)
    fields = dict(f.split(":", 1) for f in meter.handle("POW:ALL?").text.split(","))
    bad = fields["3"]
    assert bad.startswith("ERR") or float(bad) > 10
    assert all(float(fields[str(n)]) < 10 for n in (1, 2, 4, 5, 6, 7, 8))


def test_forced_whole_reply_faults():
    _, meter = make_meter()
    meter.faults.force("timeout")
    assert meter.handle("POW:ALL?").text is None
    meter.faults.force("garbled")
    assert ":" not in meter.handle("POW:ALL?").text  # no channel labels survive


def test_fault_rates_do_not_change_the_noise_values():
    _, clean = make_meter(3)
    _, faulty = make_meter(3, faults=FaultRates(channel_invalid_rate=0.5))
    pairs = [(clean.handle("POW? 1").text, faulty.handle("POW? 1").text) for _ in range(500)]
    valid_pairs = [(a, b) for a, b in pairs if not b.startswith("ERR") and float(b) < 10]
    assert 100 < len(valid_pairs) < 400  # roughly half were made invalid...
    assert all(a == b for a, b in valid_pairs)  # ...and every remaining value is untouched


def test_same_seed_same_readings():
    def readings(seed):
        _, meter = make_meter(seed)
        return [meter.handle("POW:ALL?").text for _ in range(20)]
    assert readings(7) == readings(7) and readings(7) != readings(8)


def test_bad_commands_get_error_replies():
    _, meter = make_meter()
    assert meter.handle("POW? 9").text == "ERR:BAD_CHANNEL"
    assert meter.handle("POW? x").text == "ERR:BAD_CHANNEL"
    assert meter.handle("UNIT WATT").text == "ERR:UNSUPPORTED_UNIT"
    assert meter.handle("AVG -5").text == "ERR:BAD_VALUE"
    assert meter.handle("FLY").text == "ERR:UNKNOWN_COMMAND"
