"""Whole program: real-but-failing devices, and instruments lost mid-run (design section 13.4).

Communication retries are covered in tests/sequence/test_retry_policy.py.
"""

from __future__ import annotations

import pytest

from ate.data.report import read_json
from ate.instruments.opm import OpmDriver
from ate.sequence.runner import ABORTED, COMPLETE_WITH_ERRORS
from tests.helpers import channel, load_data, raw_rows, run_with

# --------------------------------------------------------------------------- real but failing data


def test_weak_channel_fails_on_real_valid_data(tmp_path):
    result, _ = run_with(tmp_path, data=load_data("examples/weak_channel.yaml"))
    ch5 = channel(result, 5)
    assert ch5.valid_count == 50 and ch5.result == "FAIL" and ch5.fail_reasons == ["avg_below_min"]
    assert ch5.avg_dbm == pytest.approx(-4.0, abs=0.05)
    assert result.overall_result == "FAIL"


def test_temperature_never_settles_but_light_is_still_measured(tmp_path):
    result, _ = run_with(tmp_path, data=load_data("examples/tec_wont_settle.yaml"))
    cond = result.summary.conditions[0]
    assert cond.temp_result == "FAIL"
    # light still measured for troubleshooting: all valid, ~0.08 dB low because the device is 0.8 C hot
    assert all(c.valid_count == 50 and c.result == "PASS" for c in cond.channels)
    assert channel(result, 1).avg_dbm == pytest.approx(0.10 - 0.08, abs=0.03)
    assert result.overall_result == "FAIL"


def test_temperature_fail_can_skip_measurements(tmp_path):
    result, _ = run_with(tmp_path, data=load_data("examples/tec_wont_settle.yaml"),
                         sequence__temperature__continue_on_fail=False)
    measure = [r for r in raw_rows(result) if r["phase"] == "measure"]
    assert len(measure) == 400 and {r["status"] for r in measure} == {"NOT_MEASURED"}
    assert "continue_on_fail is off" in measure[0]["note"]


# --------------------------------------------------------------------------- instruments lost


def test_one_meter_lost_others_carry_on(tmp_path):
    result, _ = run_with(tmp_path, lambda b: b.kill("opm_b", after_queries=10),
                         data=load_data("examples/two_meters_16ch.yaml"))
    assert result.status == COMPLETE_WITH_ERRORS
    for number in range(1, 9):
        assert channel(result, number).result == "PASS"
    for number in range(9, 17):
        ch = channel(result, number)
        assert (ch.valid_count, ch.not_measured_count, ch.result) == (10, 40, "FAIL")
    assert "opm_b" in read_json(result.run_dir / "run_info.json")["unreachable_instruments"]


def test_every_meter_lost_aborts_but_keeps_data(tmp_path):
    result, bench = run_with(tmp_path, lambda b: b.kill("opm_a", after_queries=20))
    assert result.status == ABORTED and "every power meter" in result.abort_reason
    ch1 = channel(result, 1)
    assert (ch1.valid_count, ch1.not_measured_count) == (20, 30)
    assert (result.run_dir / "summary.csv").exists()
    assert not bench.simulator("tec_main").output_on  # TEC still switched off


def test_tec_lost_aborts_and_every_planned_reading_is_accounted_for(tmp_path):
    result, _ = run_with(tmp_path, lambda b: b.kill("tec_main", after_queries=60))
    assert result.status == ABORTED
    info = read_json(result.run_dir / "run_info.json")
    assert info["status"] == ABORTED and info["abort_reason"]
    finals = {(r["channel"], r["measurement_index"]): r["status"]
              for r in raw_rows(result) if r["phase"] == "measure"}
    assert len(finals) == 400 and "NOT_MEASURED" in finals.values()


def test_ctrl_c_stops_cleanly(tmp_path, monkeypatch):
    calls = {"n": 0}
    original = OpmDriver.read_all

    def interrupted(self):
        calls["n"] += 1
        if calls["n"] == 5:
            raise KeyboardInterrupt
        return original(self)
    monkeypatch.setattr(OpmDriver, "read_all", interrupted)
    result, bench = run_with(tmp_path)
    assert result.status == ABORTED and "Ctrl+C" in result.abort_reason
    assert channel(result, 1).valid_count == 4  # what was measured is kept
    assert not bench.simulator("tec_main").output_on  # heater switched off on the way out
