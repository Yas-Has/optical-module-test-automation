"""Whole program, end to end: complete runs and what they produce."""

from __future__ import annotations

import pytest

from ate.sequence.runner import COMPLETE
from tests.helpers import build_config, channel, comparable, load_data, raw_rows, run, summary_rows


def test_clean_run_passes_and_writes_every_file(tmp_path):
    result = run(build_config(tmp_path, clean=True))
    assert result.status == COMPLETE and result.overall_result == "PASS"
    files = {p.name for p in result.run_dir.iterdir()}
    assert {"raw_measurements.csv", "summary.csv", "run_info.json", "config_used.yaml", "run.log"} <= files

    rows = raw_rows(result)
    measure = [r for r in rows if r["phase"] == "measure"]
    assert len(measure) == 8 * 50 and all(r["status"] == "VALID" for r in measure)
    assert len([r for r in rows if r["phase"] == "monitor"]) == 50
    assert all(r["run_id"] == result.run_id and r["device_serial"] == "SN-0001" for r in rows)


def test_channel_offsets_show_up_in_the_results(tmp_path):
    result = run(build_config(tmp_path, clean=True))
    for number, offset in enumerate(load_data()["simulation"]["device"]["channel_offsets_db"], start=1):
        assert channel(result, number).avg_dbm == pytest.approx(offset, abs=0.05)


def test_each_run_gets_its_own_folder(tmp_path):
    config = build_config(tmp_path, clean=True)
    first, second = run(config), run(config)
    assert first.run_dir != second.run_dir and first.run_dir.exists() and second.run_dir.exists()


def test_same_seed_reproduces_every_value(tmp_path):
    a, b = run(build_config(tmp_path, seed=99)), run(build_config(tmp_path, seed=99))
    c = run(build_config(tmp_path, seed=100))
    assert comparable(a) == comparable(b)
    assert comparable(a) != comparable(c)


def test_seed_is_always_saved(tmp_path):
    config = build_config(tmp_path, clean=True)
    config = config.model_copy(update={"run": config.run.model_copy(update={"seed": None})})
    result = run(config)
    assert f"seed: {result.seed}" in (result.run_dir / "config_used.yaml").read_text()


def test_per_channel_mode(tmp_path):
    result = run(build_config(tmp_path, clean=True, sequence__measurement__read_mode="per_channel"))
    rows = [r for r in raw_rows(result) if r["phase"] == "measure"]
    assert result.overall_result == "PASS" and len(rows) == 400
    assert [r["channel"] for r in rows[:51]] == ["1"] * 50 + ["2"]  # one channel at a time


def test_two_temperatures_show_the_device_link(tmp_path):
    result = run(build_config(tmp_path, load_data("examples/two_temperatures.yaml"), clean=True))
    cold, hot = result.summary.conditions
    assert (cold.label, hot.label) == ("25C", "40C") and result.overall_result == "PASS"
    for number in range(1, 9):
        # 15 C cooler -> about 1.5 dB brighter
        assert channel(result, number, 0).avg_dbm - channel(result, number, 1).avg_dbm == pytest.approx(1.5, abs=0.1)


def test_two_meters_sixteen_channels(tmp_path):
    result = run(build_config(tmp_path, load_data("examples/two_meters_16ch.yaml"), clean=True))
    assert result.overall_result == "PASS"
    summary = summary_rows(result)
    assert len(summary) == 16
    assert (summary[8]["channel"], summary[8]["instrument"], summary[8]["instrument_input"]) == ("9", "opm_b", "1")


def test_adding_a_second_meter_leaves_the_first_meters_data_alone(tmp_path):
    def opm_a(result):
        return [(r["measurement_index"], r["channel"], r["status"], float(r["value"] or "nan"))
                for r in raw_rows(result) if r["instrument"] == "opm_a"]
    one = opm_a(run(build_config(tmp_path, seed=11)))
    two = opm_a(run(build_config(tmp_path, load_data("examples/two_meters_16ch.yaml"), seed=11)))
    assert [r[:3] for r in one] == [r[:3] for r in two]  # same faults, same statuses
    # Values differ only by the tiny temperature change from the extra time the second meter takes.
    assert all(abs(a[3] - b[3]) < 0.01 for a, b in zip(one, two) if a[2] == "VALID")
