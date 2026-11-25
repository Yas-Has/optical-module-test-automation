"""ate/instruments/opm/driver.py: one bad channel never spoils the others."""

from __future__ import annotations

import pytest

from ate.core.transport import ConnectionLost
from ate.data.models import Status
from ate.instruments.opm import OpmDriver, OpmSettings
from ate.instruments.opm.driver import parse_labelled_fields
from tests.helpers import LOST, TIMEOUT, ScriptedTransport, build_config


def connected_meter(*replies, device_channels=None):
    transport = ScriptedTransport(["XSIM,OPM-8,opm,1.0", *replies])
    settings = OpmSettings(name="opm_a", device_channels=device_channels or list(range(1, 9)))
    meter = OpmDriver(settings, lambda: transport)
    meter.connect()
    return meter, transport


def test_parse_labelled_fields():
    assert parse_labelled_fields("1:-0.12, 2:ERR,junk,3:") == {
        1: ("1:-0.12", "-0.12"), 2: ("2:ERR", "ERR"), 3: ("3:", "")}


def test_configure_sets_unit_and_averaging(tmp_path):
    meter, transport = connected_meter("OK", "OK")
    meter.configure(build_config(tmp_path, sequence__measurement__averaging_time_ms=250,
                                 sequence__measurement__interval_ms=250))
    assert transport.sent[1:] == ["UNIT DBM", "AVG 250"]


def test_read_all_good_reply():
    meter, transport = connected_meter("1:0.1,2:0.2,3:0.3,4:0.4,5:0.5,6:0.6,7:0.7,8:0.8")
    result = meter.read_all()
    assert transport.sent[-1] == "POW:ALL?" and result.whole_failure is None
    assert [m.value for m in result.measurements] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    assert len({m.timestamp_utc for m in result.measurements}) == 1  # measured together


def test_read_all_one_bad_channel_does_not_spoil_the_others():
    meter, _ = connected_meter("1:0.1,2:0.2,3:ERR:NO_SIGNAL,4:0.#4,5:,7:0.7,8:45.1")
    by_input = {m.instrument_input: m for m in meter.read_all().measurements}
    assert by_input[1].status is Status.VALID and by_input[2].status is Status.VALID
    assert by_input[3].status is Status.INVALID and by_input[3].raw_response == "3:ERR:NO_SIGNAL"
    assert by_input[4].status is Status.GARBLED  # damaged value
    assert by_input[5].status is Status.GARBLED  # value lost
    assert by_input[6].status is Status.GARBLED and "missing" in by_input[6].note  # field lost
    assert by_input[7].status is Status.VALID
    assert by_input[8].status is Status.INVALID and by_input[8].value == 45.1  # out of meter range


def test_read_all_unreadable_reply_is_a_whole_failure():
    meter, _ = connected_meter("#?~@!$")
    result = meter.read_all()
    assert result.whole_failure is Status.GARBLED
    assert all(m.status is Status.GARBLED and m.raw_response == "#?~@!$" for m in result.measurements)


def test_read_all_no_reply_is_a_whole_failure():
    meter, _ = connected_meter(TIMEOUT)
    result = meter.read_all()
    assert result.whole_failure is Status.TIMEOUT
    assert len(result.measurements) == 8 and all(m.status is Status.TIMEOUT for m in result.measurements)


def test_read_one_input():
    meter, transport = connected_meter("-0.25")
    m = meter.read_input(3)
    assert transport.sent[-1] == "POW? 3" and (m.status, m.value, m.channel) == (Status.VALID, -0.25, 3)


def test_inputs_map_to_device_channels():
    meter, _ = connected_meter("1:0.1,2:0.2", device_channels=[9, 10])
    measurements = meter.read_all().measurements
    assert [(m.instrument_input, m.channel) for m in measurements] == [(1, 9), (2, 10)]


def test_extra_fields_for_unwired_inputs_are_ignored():
    meter, _ = connected_meter("1:0.1,2:0.2,3:-75.0,4:-75.0", device_channels=[1, 2])
    assert [m.channel for m in meter.read_all().measurements] == [1, 2]


def test_lost_connection_is_raised_not_hidden():
    meter, _ = connected_meter(LOST)
    with pytest.raises(ConnectionLost):
        meter.read_all()
