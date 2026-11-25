"""ate/instruments/tec/driver.py: commands sent, and replies turned into Measurements."""

from __future__ import annotations

from ate.data.models import Status
from ate.instruments.tec import TecDriver, TecSettings
from tests.helpers import LOST, TIMEOUT, ScriptedTransport


def connected_tec(*replies):
    transport = ScriptedTransport(["XSIM,TEC-1000,tec,1.0", *replies])
    tec = TecDriver(TecSettings(name="tec_main"), lambda: transport)
    tec.connect()
    return tec, transport


def test_set_temperature_and_output_commands():
    tec, transport = connected_tec("OK", "OK", "OK")
    tec.set_temperature(40)
    tec.output(True)
    tec.output(False)
    assert transport.sent[1:] == ["TEMP 40.000", "OUTP ON", "OUTP OFF"]


def test_good_reading():
    tec, transport = connected_tec("39.987")
    m = tec.read_temperature()
    assert transport.sent[-1] == "TEMP?"
    assert (m.status, m.value, m.unit, m.instrument, m.instrument_type) == (
        Status.VALID, 39.987, "C", "tec_main", "sim_tec")


def test_impossible_reading_keeps_value_and_raw_reply():
    tec, _ = connected_tec("999.000")
    m = tec.read_temperature()
    assert (m.status, m.value, m.raw_response) == (Status.INVALID, 999.0, "999.000")


def test_error_reply_is_invalid():
    tec, _ = connected_tec("ERR:SENSOR_FAULT")
    assert tec.read_temperature().status is Status.INVALID


def test_damaged_reply_is_garbled():
    tec, _ = connected_tec("39.#87")
    assert tec.read_temperature().status is Status.GARBLED


def test_no_reply_is_a_timeout_not_an_exception():
    tec, _ = connected_tec(TIMEOUT)
    m = tec.read_temperature()
    assert m.status is Status.TIMEOUT and m.value is None


def test_range_comes_from_settings():
    transport = ScriptedTransport(["id", "45.0"])
    tec = TecDriver(TecSettings(name="t", range_c=(0, 42)), lambda: transport)
    tec.connect()
    assert tec.read_temperature().status is Status.INVALID  # 45 C is outside this TEC's range


def test_close_switches_the_output_off():
    tec, transport = connected_tec("OK")
    tec.close()
    assert transport.sent[-1] == "OUTP OFF" and transport.closed


def test_close_still_disconnects_if_the_tec_is_gone():
    tec, transport = connected_tec(LOST)
    tec.close()  # must not raise
    assert transport.closed and not tec.connected
