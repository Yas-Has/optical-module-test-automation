"""Whole runs with settings changed in the YAML: the measurements, the summary and the charts must
all follow the new settings, with no code changes.

Charts are checked by catching each one as it is saved and looking at what was drawn on it
(where the limit lines are, where the target band is, how many squares the status grid has).
"""

from __future__ import annotations

import statistics

import pytest

from tests.helpers import NO_FAULTS, build_config, channel, comparable, load_data, raw_rows, run, summary_rows


@pytest.fixture
def charts(monkeypatch):
    """Run with charts on, and collect every chart drawn: {"power_40C": axes, ...}."""
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure

    drawn = {}
    original = Figure.savefig

    def capture(fig, path, *args, **kwargs):
        drawn[path.stem] = fig.axes[0]
        return original(fig, path, *args, **kwargs)
    monkeypatch.setattr(Figure, "savefig", capture)
    return drawn


def horizontal_lines(ax) -> set[float]:
    return {round(line.get_ydata()[0], 3) for line in ax.lines
            if len(set(line.get_ydata())) == 1 and line.get_linestyle() == "--"}


def band(ax) -> tuple[float, float]:
    """The shaded temperature target band (the first shape drawn on the chart)."""
    rect = ax.patches[0]
    return round(rect.get_y(), 3), round(rect.get_y() + rect.get_height(), 3)


def grid_shape(ax) -> tuple[int, int]:
    return ax.collections[0].get_array().shape  # (channels, readings)


# --------------------------------------------------------------------------- limits


def test_tighter_power_limits_fail_the_dimmest_channel_and_move_the_chart_lines(tmp_path, charts):
    # Channel offsets are +0.10 -0.25 +0.05 -0.40 +0.15 -0.10 +0.20 -0.05: only ch 4 is outside +/-0.3.
    result = run(build_config(tmp_path, clean=True, run__save_plots=True,
                              limits__avg_power_dbm={"min": -0.3, "max": 0.3}))
    failed = [c.channel for c in result.summary.conditions[0].channels if c.result == "FAIL"]
    assert failed == [4] and result.overall_result == "FAIL"
    assert channel(result, 4).fail_reasons == ["avg_below_min"]
    assert horizontal_lines(charts["power_40C"]) == {-0.3, 0.3}


def test_allowing_bad_readings_changes_the_verdict_but_not_a_single_measurement(tmp_path):
    flaky = load_data("examples/flaky_meter.yaml")
    strict = run(build_config(tmp_path, flaky, seed=2))
    relaxed = run(build_config(tmp_path, flaky, seed=2, limits__max_bad_readings=5))
    assert strict.overall_result == "FAIL" and relaxed.overall_result == "PASS"
    assert comparable(strict) == comparable(relaxed)  # a limit only changes the judgement, never the data
    assert any(c.invalid_count for c in relaxed.summary.conditions[0].channels)  # bad readings really were there


def test_turning_off_require_all_valid_accepts_any_number_of_bad_readings(tmp_path):
    flaky = load_data("examples/flaky_meter.yaml")
    result = run(build_config(tmp_path, flaky, seed=2, limits__require_all_valid=False))
    assert result.overall_result == "PASS"
    assert all(r["fail_reasons"] == "" for r in summary_rows(result))


# --------------------------------------------------------------------------- measurement settings


def test_fewer_readings_per_channel(tmp_path, charts):
    result = run(build_config(tmp_path, clean=True, run__save_plots=True,
                              sequence__measurement__readings_per_channel=20))
    measure = [r for r in raw_rows(result) if r["phase"] == "measure"]
    assert len(measure) == 8 * 20 and max(int(r["measurement_index"]) for r in measure) == 20
    assert all(r["valid_count"] == "20" for r in summary_rows(result)) and result.overall_result == "PASS"
    assert grid_shape(charts["status_40C"]) == (8, 20)


def test_longer_averaging_gives_steadier_readings(tmp_path):
    def spread(averaging_ms):
        result = run(build_config(tmp_path, clean=True, simulation__device__noise_db_at_100ms=0.5,
                                  sequence__measurement__averaging_time_ms=averaging_ms,
                                  sequence__measurement__interval_ms=averaging_ms))
        return statistics.fmean(c.std_db for c in result.summary.conditions[0].channels)
    # Noise shrinks with the square root of the averaging time: 4x longer -> half the spread.
    assert spread(400) / spread(100) == pytest.approx(0.5, abs=0.1)


def test_noisier_device_shows_a_wider_spread(tmp_path):
    quiet = run(build_config(tmp_path, clean=True))
    noisy = run(build_config(tmp_path, clean=True, simulation__device__noise_db_at_100ms=0.5))
    for n in range(1, 9):
        assert channel(noisy, n).std_db > 5 * channel(quiet, n).std_db
        assert channel(noisy, n).avg_dbm == pytest.approx(channel(quiet, n).avg_dbm, abs=0.25)  # same centre


# --------------------------------------------------------------------------- temperature settings


def test_different_setpoint_and_tolerance(tmp_path, charts):
    result = run(build_config(tmp_path, clean=True, run__save_plots=True,
                              sequence__conditions=[{"setpoint_c": 30.0}], sequence__temperature__tolerance_c=0.2))
    cond = result.summary.conditions[0]
    assert cond.label == "30C" and cond.temp_result == "PASS"
    assert cond.temp_mean_c == pytest.approx(30.0, abs=0.2)
    # 10 C cooler than the 40 C reference at -0.1 dB/C -> every channel about 1 dB brighter.
    offsets = load_data()["simulation"]["device"]["channel_offsets_db"]
    for n, offset in enumerate(offsets, start=1):
        assert channel(result, n).avg_dbm == pytest.approx(offset + 1.0, abs=0.05)
    assert band(charts["temperature_30C"]) == (29.8, 30.2)
    assert set(charts) == {"power_30C", "temperature_30C", "status_30C"}


def test_no_temperature_effect_when_the_coefficient_is_zero(tmp_path):
    data = load_data("examples/two_temperatures.yaml")
    result = run(build_config(tmp_path, data, clean=True, simulation__device__temp_coefficient_db_per_c=0.0))
    for n in range(1, 9):
        assert channel(result, n, 0).avg_dbm == pytest.approx(channel(result, n, 1).avg_dbm, abs=0.05)


def test_a_stability_rule_stricter_than_the_tec_can_hold_fails_verification(tmp_path):
    # The TEC wobbles by about 0.02 C per reading; demanding under 0.005 C of drift can never be met.
    result = run(build_config(tmp_path, clean=True, sequence__temperature__max_drift_c=0.005))
    cond = result.summary.conditions[0]
    assert cond.temp_result == "FAIL" and cond.tecs[0].settle_attempts == 2
    assert all(c.result == "PASS" for c in cond.channels)  # light was still measured, and was fine
    assert result.overall_result == "FAIL"


def test_more_settle_attempts_are_used_when_allowed(tmp_path):
    data = load_data("examples/tec_wont_settle.yaml")
    result = run(build_config(tmp_path, data, clean=True, sequence__temperature__max_settle_attempts=4))
    assert result.summary.conditions[0].tecs[0].settle_attempts == 4
    attempts = {r["settle_attempt"] for r in raw_rows(result) if r["phase"] == "settle"}
    assert attempts == {"1", "2", "3", "4"}


# --------------------------------------------------------------------------- instruments and device


def test_no_retries_means_no_second_attempts(tmp_path):
    flaky = load_data("examples/flaky_meter.yaml")
    flaky["instruments"][1]["max_comm_retries"] = 0
    result = run(build_config(tmp_path, flaky, seed=2))
    measure = [r for r in raw_rows(result) if r["phase"] == "measure"]
    assert all(r["attempt"] == "1" for r in measure)
    assert sum(c.timeout_count + c.garbled_count for c in result.summary.conditions[0].channels) > 0


def test_more_retries_recover_more_readings(tmp_path):
    def unrecovered(retries):
        flaky = load_data("examples/flaky_meter.yaml")
        flaky["instruments"][1]["max_comm_retries"] = retries
        result = run(build_config(tmp_path, flaky, seed=2))
        return sum(c.timeout_count + c.garbled_count for c in result.summary.conditions[0].channels)
    assert unrecovered(0) > unrecovered(1) >= unrecovered(3)


def test_a_four_channel_device(tmp_path, charts):
    data = load_data()
    data["simulation"]["device"].update(channels=4, channel_offsets_db=[0.0, 0.1, -0.1, 0.2])
    data["instruments"][1]["device_channels"] = [1, 2, 3, 4]
    result = run(build_config(tmp_path, data, clean=True, run__save_plots=True))
    assert [r["channel"] for r in summary_rows(result)] == ["1", "2", "3", "4"]
    assert result.overall_result == "PASS"
    assert grid_shape(charts["status_40C"]) == (4, 50)
    assert len(charts["power_40C"].get_xticks()) == 4


def test_a_dimmer_device_fails_every_channel(tmp_path):
    result = run(build_config(tmp_path, clean=True, simulation__device__nominal_power_dbm=-5.0))
    assert all(r["channel_result"] == "FAIL" for r in summary_rows(result))
    assert all("avg_below_min" in c.fail_reasons for c in result.summary.conditions[0].channels)


def test_faults_turned_off_in_the_yaml_give_a_perfect_run(tmp_path):
    data = load_data("examples/flaky_meter.yaml")
    for inst in data["instruments"]:
        inst.setdefault("simulation", {})["faults"] = dict(NO_FAULTS)
    result = run(build_config(tmp_path, data, seed=2))
    assert all(r["status"] == "VALID" and r["attempt"] == "1" for r in raw_rows(result))
