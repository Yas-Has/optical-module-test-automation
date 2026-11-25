"""ate/data/analysis.py: statistics and pass/fail rules, checked against hand-calculated values."""

from __future__ import annotations

import math

import pytest

from ate.data.analysis import (
    FAIL,
    PASS,
    RawRow,
    analyze,
    dbm_to_mw,
    final_attempts,
    mw_to_dbm,
    power_average_dbm,
)
from ate.data.models import Status
from tests.helpers import build_config, load_data


def test_dbm_conversions():
    assert dbm_to_mw(0) == 1.0
    assert dbm_to_mw(3) == pytest.approx(1.995, abs=1e-3)  # +3 dBm is about double
    assert mw_to_dbm(0.5) == pytest.approx(-3.01, abs=1e-2)  # half is about -3 dBm
    assert mw_to_dbm(dbm_to_mw(-1.234)) == pytest.approx(-1.234)


def test_power_average_is_done_in_milliwatts():
    # 0 dBm (1 mW) and -10 dBm (0.1 mW): the true average power is 0.55 mW = -2.60 dBm,
    # not the -5 dBm a plain average of the dBm numbers would give.
    assert power_average_dbm([0.0, -10.0]) == pytest.approx(10 * math.log10(0.55))


def test_power_average_barely_differs_for_small_variations():
    values = [0.05, -0.07, 0.02, -0.01, 0.08]
    assert abs(power_average_dbm(values) - sum(values) / len(values)) < 0.001


# --------------------------------------------------------------------------- pass/fail rules
# A one-channel device with 3 readings per channel keeps the hand-built data small.


def one_channel_config(tmp_path, **dotted):
    data = load_data()
    data["simulation"]["device"].update(channels=1, channel_offsets_db=[0.0])
    data["instruments"][1]["device_channels"] = [1]
    return build_config(tmp_path, data, sequence__measurement__readings_per_channel=3, **dotted)


def settle_rows(values, attempt=1):
    return [RawRow("40C", "settle", "tec_main", None, None, i, attempt, 1, v, Status.VALID)
            for i, v in enumerate(values, start=1)]


STABLE = settle_rows([40.0] * 11)


def meas(index, value, status=Status.VALID, attempt=1):
    return RawRow("40C", "measure", "opm_a", 1, 1, index, None, attempt, value, status)


def analyse(tmp_path, rows, **dotted):
    return analyze(STABLE + rows, one_channel_config(tmp_path, **dotted), "run", "SN")


@pytest.mark.parametrize("value, result", [(3.0, PASS), (-3.0, PASS), (3.001, FAIL), (-3.001, FAIL)])
def test_average_limits_are_inclusive(tmp_path, value, result):
    summary = analyse(tmp_path, [meas(i, value) for i in (1, 2, 3)])
    assert summary.conditions[0].channels[0].result == result


def test_statistics(tmp_path):
    ch = analyse(tmp_path, [meas(1, 0.1), meas(2, -0.1), meas(3, 0.3)]).conditions[0].channels[0]
    assert (ch.min_dbm, ch.max_dbm) == (-0.1, 0.3)
    assert ch.std_db == pytest.approx(0.2)  # sample standard deviation
    assert ch.avg_dbm == pytest.approx(power_average_dbm([0.1, -0.1, 0.3]))


def test_one_invalid_reading_fails_the_channel_and_is_excluded_from_stats(tmp_path):
    ch = analyse(tmp_path, [meas(1, 0.0), meas(2, 45.0, Status.INVALID), meas(3, 0.0)]).conditions[0].channels[0]
    assert ch.result == FAIL and ch.invalid_count == 1 and ch.valid_count == 2
    assert ch.max_dbm == 0.0  # the impossible 45 dBm did not pollute the statistics
    assert "invalid_readings(1/3)" in ch.fail_reasons


def test_no_valid_readings(tmp_path):
    ch = analyse(tmp_path, [meas(i, None, Status.TIMEOUT) for i in (1, 2, 3)]).conditions[0].channels[0]
    assert ch.result == FAIL and ch.avg_dbm is None and ch.fail_reasons[0] == "no_valid_readings"
    assert ch.timeout_count == 3


def test_missing_readings_are_counted_not_ignored(tmp_path):
    ch = analyse(tmp_path, [meas(1, 0.0)]).conditions[0].channels[0]  # 2 of 3 readings have no row at all
    assert ch.not_measured_count == 2 and ch.result == FAIL


def test_require_all_valid_can_be_relaxed(tmp_path):
    rows = [meas(1, 0.0), meas(2, None, Status.INVALID), meas(3, 0.0)]
    assert analyse(tmp_path, rows, limits__require_all_valid=False).conditions[0].channels[0].result == PASS


def test_only_the_final_attempt_counts(tmp_path):
    rows = [meas(1, None, Status.TIMEOUT, attempt=1), meas(1, 0.0, attempt=2), meas(2, 0.0), meas(3, 0.0)]
    ch = analyse(tmp_path, rows).conditions[0].channels[0]
    assert ch.result == PASS and ch.valid_count == 3 and ch.timeout_count == 0 and ch.retries_used == 1
    assert len(final_attempts(rows)) == 3


def test_high_retry_rate_warns_but_does_not_fail(tmp_path):
    rows = [r for i in (1, 2, 3) for r in (meas(i, None, Status.GARBLED), meas(i, 0.0, attempt=2))]
    ch = analyse(tmp_path, rows).conditions[0].channels[0]
    assert ch.result == PASS and ch.warnings == ["high_retry_rate(3)"]


def test_device_fails_if_temperature_fails(tmp_path):
    rows = settle_rows([41.0] * 20) + [meas(i, 0.0) for i in (1, 2, 3)]
    summary = analyze(rows, one_channel_config(tmp_path), "run", "SN")
    assert summary.conditions[0].temp_result == FAIL
    assert summary.conditions[0].channels[0].result == PASS  # the channel itself is fine
    assert summary.overall_result == FAIL


def test_temperature_drift_during_measurement_warns(tmp_path):
    monitor = [RawRow("40C", "monitor", "tec_main", None, None, 1, None, 1, 40.7, Status.VALID)]
    summary = analyse(tmp_path, [meas(i, 0.0) for i in (1, 2, 3)] + monitor)
    assert summary.conditions[0].warnings == ["temp_drift_during_measurement"]
    assert summary.overall_result == PASS  # a warning, not a failure
