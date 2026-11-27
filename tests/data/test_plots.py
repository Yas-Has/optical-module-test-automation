"""ate/data/plots.py: the per-run charts, and that charts can never break a run."""

from __future__ import annotations

import builtins

import pytest

from ate.cli import main
from ate.data import plots
from ate.data.analysis import RawRow, final_attempts, load_raw_rows
from ate.data.models import Status
from tests.helpers import CONFIG_DIR, build_config, load_data, run

pytest.importorskip("matplotlib")


def _row(status: Status, attempt: int = 1) -> RawRow:
    return RawRow("40C", "measure", "opm_a", 1, 1, 1, None, attempt, 0.0, status)


def test_each_reading_lands_in_the_right_colour_group():
    assert plots.grid_category(_row(Status.VALID), retried=False) == 0
    assert plots.grid_category(_row(Status.VALID, attempt=2), retried=True) == 1
    assert plots.grid_category(_row(Status.TIMEOUT, attempt=2), retried=True) == 2
    assert plots.grid_category(_row(Status.GARBLED), retried=False) == 2
    assert plots.grid_category(_row(Status.INVALID), retried=False) == 3
    assert plots.grid_category(_row(Status.NOT_MEASURED), retried=False) == 4
    assert plots.grid_category(None, retried=False) == 4


def test_status_grid_agrees_with_the_summary(tmp_path):
    result = run(build_config(tmp_path, load_data("examples/flaky_meter.yaml"), seed=7))
    rows = load_raw_rows(result.run_dir / "raw_measurements.csv")
    cond = result.summary.conditions[0]
    matrix = plots.status_matrix(rows, final_attempts(rows), cond, 50)
    for line, channel in zip(matrix, cond.channels):
        assert len(line) == 50
        assert line.count(0) + line.count(1) == channel.valid_count
        assert line.count(3) == channel.invalid_count
    assert any(1 in line for line in matrix)  # the flaky meter really did need retries


def test_a_run_saves_three_charts_per_condition(tmp_path):
    result = run(build_config(tmp_path, load_data("examples/two_temperatures.yaml"), clean=True,
                              run__save_plots=True))
    names = sorted(p.name for p in result.run_dir.glob("*.png"))
    assert names == ["power_25C.png", "power_40C.png", "status_25C.png", "status_40C.png",
                     "temperature_25C.png", "temperature_40C.png"]
    assert all((result.run_dir / n).read_bytes().startswith(b"\x89PNG") for n in names)


def test_charts_turned_off_in_config_are_not_drawn(tmp_path):
    result = run(build_config(tmp_path, clean=True, run__save_plots=False))
    assert not list(result.run_dir.glob("*.png"))


def _matplotlib_import_fails(monkeypatch, error: Exception) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "matplotlib" or name.startswith("matplotlib."):
            raise error
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_missing_matplotlib_skips_charts_and_the_run_still_passes(tmp_path, monkeypatch):
    _matplotlib_import_fails(monkeypatch, ModuleNotFoundError("No module named 'matplotlib'", name="matplotlib"))
    result = run(build_config(tmp_path, clean=True, run__save_plots=True))
    assert result.overall_result == "PASS" and not list(result.run_dir.glob("*.png"))
    assert "Charts skipped: matplotlib is not installed" in (result.run_dir / "run.log").read_text()


def test_matplotlib_that_is_installed_but_broken_is_not_called_missing(monkeypatch):
    # e.g. NumPy 2.4+ on a CPU (or VM) without the features it needs
    _matplotlib_import_fails(monkeypatch, RuntimeError("NumPy was built with baseline optimizations: \n(X86_V2)"))
    problem = plots.why_no_plots()
    assert problem == "matplotlib could not be loaded (RuntimeError: NumPy was built with baseline optimizations: (X86_V2))"
    assert "not installed" not in problem


def test_a_drawing_error_never_fails_the_run(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("bad chart")
    monkeypatch.setattr(plots, "write_plots", broken)
    result = run(build_config(tmp_path, clean=True, run__save_plots=True))
    assert result.status == "COMPLETE" and result.overall_result == "PASS"
    assert (result.run_dir / "summary.csv").is_file()
    assert "Charts could not be drawn" in (result.run_dir / "run.log").read_text()


def test_plot_command_draws_charts_for_an_old_run(tmp_path, capsys):
    result = run(build_config(tmp_path, clean=True))  # saved without charts
    assert main(["plot", str(result.run_dir)]) == 0
    assert sorted(p.name for p in result.run_dir.glob("*.png")) == [
        "power_40C.png", "status_40C.png", "temperature_40C.png"]
    assert "Saved" in capsys.readouterr().out


def test_plot_command_on_a_folder_without_data_is_an_error(tmp_path):
    assert main(["plot", str(tmp_path)]) == 2


def test_the_power_chart_label_shows_how_many_bad_readings_are_allowed(tmp_path):
    assert plots._allowance(build_config(tmp_path)) == ""
    assert "Up to 2 bad reading(s) allowed" in plots._allowance(build_config(tmp_path, limits__max_bad_readings=2))
    assert "Any number" in plots._allowance(build_config(tmp_path, limits__require_all_valid=False))


def test_reprocess_with_new_limits_draws_new_charts_and_keeps_the_originals(tmp_path, capsys):
    result = run(build_config(tmp_path, clean=True, run__save_plots=True))
    originals = {p.name: p.read_bytes() for p in result.run_dir.glob("*.png")}
    strict = tmp_path / "strict.yaml"
    strict.write_text(f"extends: {CONFIG_DIR / 'default.yaml'}\nlimits:\n  avg_power_dbm: {{min: -0.1, max: 0.1}}\n")

    assert main(["reprocess", str(result.run_dir), "--config", str(strict)]) == 1  # ch 4 now fails
    summary_file = next(result.run_dir.glob("summary_reprocessed_*.csv"))
    suffix = summary_file.stem.removeprefix("summary")
    new = sorted(p.name for p in result.run_dir.glob(f"*{suffix}.png"))
    assert new == [f"power_40C{suffix}.png", f"status_40C{suffix}.png", f"temperature_40C{suffix}.png"]
    assert {p.name: p.read_bytes() for p in result.run_dir.glob("*_40C.png")} == originals
    assert "Chart written to" in capsys.readouterr().out
