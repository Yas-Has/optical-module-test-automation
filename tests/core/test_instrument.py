"""ate/core/instrument.py: how replies are judged, and the lifecycle every instrument shares."""

from __future__ import annotations

import pytest

from ate.core.instrument import Instrument, InstrumentError, classify_value
from ate.core.settings import InstrumentConfigBase
from ate.data.models import Status
from tests.helpers import LOST, TIMEOUT, ScriptedTransport


@pytest.mark.parametrize("text, status, value", [
    ("40.012", Status.VALID, 40.012),
    ("  -3.5 ", Status.VALID, -3.5),
    ("ERR:SENSOR_FAULT", Status.INVALID, None),
    ("999.000", Status.INVALID, 999.0),  # impossible, but the value is kept
    ("nan", Status.INVALID, None),
    ("inf", Status.INVALID, None),
    ("4#.012", Status.GARBLED, None),
    ("", Status.GARBLED, None),
    (None, Status.TIMEOUT, None),
])
def test_classify_value(text, status, value):
    assert classify_value(text, (-10, 100)) == (status, value)


def test_out_of_spec_is_still_valid():
    # -4.2 dBm fails the test limits, but it is a real reading, so VALID
    assert classify_value("-4.2", (-60, 10)) == (Status.VALID, -4.2)


# --------------------------------------------------------------------------- lifecycle


class Probe(Instrument):
    """The smallest possible instrument, to test the shared behaviour on its own."""

    shutdown_fails = False
    shut_down = False

    def _safe_shutdown(self) -> None:
        if self.shutdown_fails:
            raise RuntimeError("instrument would not switch off")
        self.shut_down = True


def probe(*replies, retries=1):
    transport = ScriptedTransport(["PROBE,1.0", *replies])
    instrument = Probe(InstrumentConfigBase(name="probe", type="probe", max_comm_retries=retries), lambda: transport)
    return instrument, transport


def test_connect_asks_for_identity():
    instrument, transport = probe()
    instrument.connect()
    assert transport.sent == ["*IDN?"] and instrument.identity == "PROBE,1.0" and instrument.connected


def test_close_makes_safe_then_disconnects_and_is_safe_twice():
    instrument, transport = probe()
    instrument.connect()
    instrument.close()
    instrument.close()
    assert instrument.shut_down and transport.closed and not instrument.connected


def test_close_still_disconnects_if_making_safe_fails():
    instrument, transport = probe()
    instrument.shutdown_fails = True
    instrument.connect()
    instrument.close()  # must not raise
    assert transport.closed and not instrument.connected


def test_context_manager_always_closes():
    instrument, transport = probe()
    with pytest.raises(ValueError):
        with instrument:
            raise ValueError("something went wrong mid-test")
    assert transport.closed


def test_setup_command_rejected():
    instrument, _ = probe("ERR:BAD_VALUE")
    instrument.connect()
    with pytest.raises(InstrumentError, match="rejected"):
        instrument.send_setup("SET 1")


def test_setup_command_retried_then_gives_up():
    instrument, transport = probe(TIMEOUT, TIMEOUT)
    instrument.connect()
    with pytest.raises(InstrumentError, match="after 2 attempts"):
        instrument.send_setup("SET 1")
    assert transport.sent.count("SET 1") == 2


def test_setup_command_recovers_after_one_timeout():
    instrument, _ = probe(TIMEOUT, "OK")
    instrument.connect()
    instrument.send_setup("SET 1")


def test_measurement_query_makes_one_attempt_and_reports_timeout_as_none():
    instrument, transport = probe(TIMEOUT)
    instrument.connect()
    assert instrument._measure_query("READ?") is None and transport.sent.count("READ?") == 1


def test_failed_identity_leaves_nothing_connected():
    instrument, transport = probe()
    transport.replies = [LOST]
    with pytest.raises(Exception):
        instrument.connect()
    assert not instrument.connected and transport.closed
