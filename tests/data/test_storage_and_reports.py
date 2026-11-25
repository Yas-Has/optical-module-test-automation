"""ate/data/recorder.py and report.py: raw data written live, summaries, run info, re-processing."""

from __future__ import annotations

import csv

from ate.data.models import Measurement, Status
from ate.data.recorder import RAW_COLUMNS, RawRecorder
from ate.data.report import SUMMARY_COLUMNS, format_summary, read_json, reprocess_run
from tests.helpers import CONFIG_DIR, build_config, run, summary_rows


def test_each_row_is_on_disk_immediately(tmp_path):
    path = tmp_path / "raw.csv"
    recorder = RawRecorder(path, "run-1", "SN-1")
    recorder.write(Measurement("opm_a", "sim_opm_8ch", "dBm", Status.INVALID, value=45.0, raw_response="3:45.0",
                               channel=3, instrument_input=3, measurement_index=7, phase="measure"))
    rows = list(csv.DictReader(open(path)))  # read while the recorder is still open
    assert list(rows[0]) == RAW_COLUMNS
    assert (rows[0]["status"], rows[0]["value"], rows[0]["raw_response"]) == ("INVALID", "45.0000", "3:45.0")
    recorder.close()


def test_missing_values_are_blank_not_zero(tmp_path):
    path = tmp_path / "raw.csv"
    with RawRecorder(path, "run-1", "SN-1") as recorder:
        recorder.write(Measurement("opm_a", "sim_opm_8ch", "dBm", Status.TIMEOUT))
    row = next(csv.DictReader(open(path)))
    assert row["value"] == "" and row["raw_response"] == ""


def test_summary_and_run_info(tmp_path):
    result = run(build_config(tmp_path, clean=True))
    summary = summary_rows(result)
    assert list(summary[0]) == SUMMARY_COLUMNS
    assert [int(r["channel"]) for r in summary] == list(range(1, 9))
    assert all(r["overall_result"] == "PASS" and r["averaging_time_ms"] == "100" for r in summary)

    info = read_json(result.run_dir / "run_info.json")
    assert info["status"] == "COMPLETE" and info["seed"] == 1 and info["overall_result"] == "PASS"
    assert [i["name"] for i in info["instruments"]] == ["tec_main", "opm_a"]
    assert info["instruments"][0]["identity"].startswith("XSIM,TEC-1000")
    assert "OVERALL: PASS" in format_summary(result.summary)


def test_reprocess_gives_an_identical_summary(tmp_path):
    result = run(build_config(tmp_path, seed=5))
    original = (result.run_dir / "summary.csv").read_text()
    summary, out = reprocess_run(result.run_dir)
    assert out.read_text() == original and summary.overall_result == result.overall_result


def test_reprocess_with_new_limits_never_overwrites_the_original(tmp_path):
    result = run(build_config(tmp_path, clean=True))
    original = (result.run_dir / "summary.csv").read_text()
    strict = tmp_path / "strict.yaml"
    strict.write_text(f"extends: {CONFIG_DIR / 'default.yaml'}\nlimits:\n  avg_power_dbm: {{min: -0.1, max: 0.1}}\n")
    summary, out = reprocess_run(result.run_dir, strict)
    assert out.name.startswith("summary_reprocessed_")
    assert (result.run_dir / "summary.csv").read_text() == original
    assert summary.overall_result == "FAIL"  # channel 4 sits at -0.40 dB
