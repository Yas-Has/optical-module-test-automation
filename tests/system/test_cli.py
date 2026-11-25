"""Whole program from the command line: exit codes and error messages."""

from __future__ import annotations

from ate.cli import main
from tests.helpers import CONFIG_DIR


def test_exit_code_reflects_the_device_result(tmp_path):
    common = ["run", "--mode", "in_process", "--results-dir", str(tmp_path), "--seed", "2"]
    assert main(common + ["--config", str(CONFIG_DIR / "default.yaml")]) == 0  # PASS
    assert main(common + ["--config", str(CONFIG_DIR / "examples/weak_channel.yaml")]) == 1  # FAIL


def test_validate(capsys):
    assert main(["validate", "--config", str(CONFIG_DIR / "examples/two_meters_16ch.yaml")]) == 0
    assert "opm_b (sim_opm_8ch)" in capsys.readouterr().out


def test_bad_config_creates_nothing(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text(f"extends: {CONFIG_DIR / 'default.yaml'}\nlimits:\n  avg_power_dbm: {{min: 5, max: 1}}\n")
    results = tmp_path / "results"
    assert main(["run", "--config", str(bad), "--results-dir", str(results)]) == 2
    assert "must be below max" in capsys.readouterr().err
    assert not results.exists()


def test_reprocess_missing_folder(tmp_path, capsys):
    assert main(["reprocess", str(tmp_path / "nope")]) == 2
    assert "no raw_measurements.csv" in capsys.readouterr().err
