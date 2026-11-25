"""ate/instruments/tec/simulator.py: the simulated TEC follows the thermal model in the design."""

from __future__ import annotations

import math

import pytest

from ate.config import DeviceModelConfig
from ate.core.settings import FaultRates
from ate.instruments.tec import TecSettings, TecSimulator
from ate.instruments.tec.settings import TecSimSettings
from ate.simulation.clock import SimClock
from ate.simulation.device import VirtualDevice


def make_tec(seed=1, *, jitter=0.0, sse=0.0, tau=8.0, faults=None):
    clock = SimClock()
    device = VirtualDevice(DeviceModelConfig())
    settings = TecSimSettings(time_constant_s=tau, jitter_c=jitter, steady_state_error_c=sse,
                              faults=faults or FaultRates())
    return clock, TecSimulator(TecSettings(name="tec", simulation=settings), device, clock, seed)


def read(tec) -> float:
    return float(tec.handle("TEMP?").text)


def heat(tec, celsius=40):
    assert tec.handle(f"TEMP {celsius}").text == "OK"
    assert tec.handle("OUTP ON").text == "OK"


def test_starts_at_room_temperature():
    _, tec = make_tec()
    assert read(tec) == pytest.approx(25.0)


def test_does_not_heat_until_switched_on():
    clock, tec = make_tec()
    tec.handle("TEMP 40")
    clock.advance(100)
    assert read(tec) == pytest.approx(25.0)


def test_settles_exponentially():
    clock, tec = make_tec()
    heat(tec)
    clock.advance(8.0)  # one time constant
    assert read(tec) == pytest.approx(40 - 15 / math.e, abs=1e-3)
    clock.advance(100)
    assert read(tec) == pytest.approx(40.0, abs=1e-3)


def test_steady_state_error():
    clock, tec = make_tec(sse=0.8)
    heat(tec)
    clock.advance(200)
    assert read(tec) == pytest.approx(40.8, abs=1e-3)


def test_switching_off_cools_back_to_room_temperature():
    clock, tec = make_tec()
    heat(tec)
    clock.advance(200)
    tec.handle("OUTP OFF")
    clock.advance(200)
    assert read(tec) == pytest.approx(25.0, abs=1e-3)


def test_jitter_matches_setting():
    clock, tec = make_tec(jitter=0.02)
    heat(tec)
    clock.advance(500)
    values = [read(tec) for _ in range(3000)]
    spread = math.sqrt(sum((v - 40) ** 2 for v in values) / len(values))
    assert spread == pytest.approx(0.02, rel=0.1)


def test_status_queries():
    _, tec = make_tec()
    heat(tec, 37.5)
    assert tec.handle("TEMP:SET?").text == "37.500" and tec.handle("OUTP?").text == "ON"
    assert tec.handle("*IDN?").text.startswith("XSIM,TEC-1000")


def test_bad_commands_get_error_replies():
    _, tec = make_tec()
    assert tec.handle("FLY").text == "ERR:UNKNOWN_COMMAND"
    assert tec.handle("TEMP hot").text == "ERR:BAD_VALUE"
    assert tec.handle("OUTP MAYBE").text == "ERR:BAD_VALUE"


def test_forced_faults():
    _, tec = make_tec()
    tec.faults.force("timeout")
    assert tec.handle("TEMP?").text is None  # silent
    tec.faults.force("invalid")
    assert tec.handle("TEMP?").text in ("ERR:SENSOR_FAULT", "999.000")
    tec.faults.force("garbled")
    with pytest.raises(ValueError):
        float(tec.handle("TEMP?").text)


def test_same_seed_same_readings():
    def readings(seed):
        clock, tec = make_tec(seed, jitter=0.02)
        heat(tec)
        clock.advance(100)
        return [tec.handle("TEMP?").text for _ in range(20)]
    assert readings(3) == readings(3) and readings(3) != readings(4)
