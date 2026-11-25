"""Charts for one run, drawn from the saved raw data (so they work on old runs too).

For every test condition:
  power_<condition>.png        box and whisker of each channel's readings, with the limits
  temperature_<condition>.png  the TEC temperature over time, with the target band
  status_<condition>.png       every planned reading as a square, coloured by what happened

matplotlib is optional (``pip install ".[plots]"``). Everything else in the program works without it.
"""

from __future__ import annotations

import re
from pathlib import Path

from ate.config import AppConfig, Condition
from ate.data.analysis import ConditionResult, RawRow, RunSummary, final_attempts
from ate.data.models import Status

# Colours (from one consistent palette; status colours are only ever used for statuses).
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6"]  # one per TEC, in fixed order
BAND = "#cde2fb"
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
NOT_MEASURED_COLOUR = "#c3c2b7"

# Status grid: (label, colour) per category, in legend order.
GRID_CATEGORIES = [
    ("valid", GOOD),
    ("valid after retry", WARNING),
    ("timeout / garbled (retries used up)", SERIOUS),
    ("invalid", CRITICAL),
    ("not measured", NOT_MEASURED_COLOUR),
]


def plots_available() -> bool:
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return False
    return True


def _safe(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", label)


def _figure(width: float, height: float):
    from matplotlib.figure import Figure  # no pyplot: no window, no global state

    fig = Figure(figsize=(width, height), dpi=120, facecolor=SURFACE)
    ax = fig.add_subplot()
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=TEXT_2, labelsize=9)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def _title(ax, text: str, subtitle: str) -> None:
    ax.set_title(text, loc="left", fontsize=12, color=TEXT, fontweight="bold", pad=22)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9, color=TEXT_2, va="bottom")


# --------------------------------------------------------------------------- 1. power


def _allowance(config: AppConfig) -> str:
    """How many bad readings the limits allow, shown under each channel when it isn't zero."""
    limits = config.limits
    if not limits.require_all_valid:
        return " Any number of bad readings allowed."
    if limits.max_bad_readings:
        return f" Up to {limits.max_bad_readings} bad reading(s) allowed per channel."
    return ""


def plot_power(path: Path, finals: list[RawRow], cond: ConditionResult, config: AppConfig) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    limits = config.limits.avg_power_dbm
    channels = cond.channels
    data = [
        [r.value for r in finals if r.condition == cond.label and r.phase == "measure"
         and r.channel == ch.channel and r.status is Status.VALID and r.value is not None]
        for ch in channels
    ]
    fig, ax = _figure(max(9.0, 0.8 * len(channels) + 3), 5.2)
    positions = list(range(1, len(channels) + 1))
    with_data = [(p, d) for p, d in zip(positions, data) if d]
    if with_data:
        boxes = ax.boxplot(
            [d for _, d in with_data], positions=[p for p, _ in with_data], widths=0.5,
            patch_artist=True, showfliers=True,
            medianprops={"color": TEXT, "linewidth": 1.2},
            whiskerprops={"color": TEXT_2, "linewidth": 1},
            capprops={"color": TEXT_2, "linewidth": 1},
            flierprops={"marker": "o", "markersize": 3, "markerfacecolor": TEXT_2, "markeredgecolor": "none"},
        )
        by_position = {p: ch for p, ch in zip(positions, channels)}
        for (p, _), box in zip(with_data, boxes["boxes"]):
            failed = by_position[p].result != "PASS"
            box.set_facecolor("#f6d4d4" if failed else BAND)
            box.set_edgecolor(CRITICAL if failed else SERIES[0])
            box.set_linewidth(1.6 if failed else 1.0)

    # The official average (calculated in milliwatts): this is the number pass/fail used.
    avgs = [(p, ch.avg_dbm) for p, ch in zip(positions, channels) if ch.avg_dbm is not None]
    if avgs:
        ax.scatter([p for p, _ in avgs], [a for _, a in avgs], marker="D", s=30, color=TEXT,
                   edgecolors=SURFACE, linewidths=1, zorder=5)

    for value, text in ((limits.max, f"upper limit {limits.max:+g} dBm"), (limits.min, f"lower limit {limits.min:+g} dBm")):
        ax.axhline(value, color=CRITICAL, linestyle="--", linewidth=1.2)
        ax.text(len(channels) + 0.55, value, text, color=CRITICAL, fontsize=8, va="bottom", ha="right")

    ax.set_xticks(positions)
    ax.set_xticklabels(
        [f"ch {ch.channel}\n{ch.valid_count}/{ch.readings_expected}\n{ch.result}" for ch in channels],
        fontsize=8,
    )
    for label, ch in zip(ax.get_xticklabels(), channels):
        if ch.result != "PASS":
            label.set_color(CRITICAL)
            label.set_fontweight("bold")
    ax.set_xlim(0.4, len(channels) + 0.6)
    values = [v for d in data for v in d] + [limits.min, limits.max]
    span = max(values) - min(values)
    ax.set_ylim(min(values) - 0.08 * span, max(values) + 0.08 * span)
    ax.set_ylabel("optical power (dBm)", color=TEXT_2, fontsize=9)

    failed = sum(1 for ch in channels if ch.result != "PASS")
    _title(ax, f"Optical power per channel, {cond.label}",
           f"{len(channels) - failed} of {len(channels)} channels pass.{_allowance(config)}")
    ax.text(0.5, -0.29, "Box = middle half of the valid readings, line = median, whiskers = spread, "
            "dots = outliers. Bad readings are never drawn.", transform=ax.transAxes, ha="center",
            va="top", fontsize=8, color=MUTED)
    ax.legend(
        handles=[Patch(facecolor=BAND, edgecolor=SERIES[0], label="channel passed"),
                 Patch(facecolor="#f6d4d4", edgecolor=CRITICAL, label="channel failed"),
                 Line2D([], [], marker="D", color=TEXT, linestyle="none", markersize=5,
                        label="average (in mW, used for pass/fail)")],
        loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=3, frameon=False, fontsize=8, labelcolor=TEXT_2,
    )
    ax.text(-0.01, -0.055, "valid", transform=ax.transAxes, ha="right", va="top", fontsize=8, color=MUTED)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)


# --------------------------------------------------------------------------- 2. temperature


def plot_temperature(path: Path, finals: list[RawRow], cond: ConditionResult, condition: Condition,
                     config: AppConfig) -> None:
    from matplotlib.patches import Patch

    tol = config.sequence.temperature.tolerance_c
    fig, ax = _figure(9.0, 4.6)
    ax.axhspan(condition.setpoint_c - tol, condition.setpoint_c + tol, color=BAND, zorder=0)
    ax.axhline(condition.setpoint_c, color=SERIES[0], linewidth=0.8, linestyle=":", zorder=1)

    rows = [r for r in finals if r.condition == cond.label and r.phase in ("settle", "monitor")]
    # Fall back to reading order if a row has no time (should not happen with any clock we ship).
    def x(row: RawRow, i: int) -> float:
        return row.elapsed_s if row.elapsed_s is not None else float(i)

    measure = [x(r, i) for i, r in enumerate(finals) if r.condition == cond.label and r.phase == "measure"]
    if measure:
        ax.axvspan(min(measure), max(measure), color=GRID, alpha=0.6, zorder=0)

    for n, verification in enumerate(cond.tecs):
        colour = SERIES[n % len(SERIES)]
        tec_rows = [r for r in rows if r.instrument == verification.tec]
        points = [(x(r, i), r.value) for i, r in enumerate(tec_rows)
                  if r.status is Status.VALID and r.value is not None]
        if points:
            ax.plot([p for p, _ in points], [v for _, v in points], color=colour, linewidth=2,
                    label=f"{verification.tec}: {verification.result}"
                          + (f", {verification.mean_c:.2f} °C" if verification.mean_c is not None else ""))
        bad = [x(r, i) for i, r in enumerate(tec_rows) if r.status is not Status.VALID]
        if bad:
            ax.scatter(bad, [condition.setpoint_c] * len(bad), marker="x", color=CRITICAL, s=30, zorder=6,
                       label=f"{verification.tec}: failed reading")
        # Mark where each new settling attempt starts.
        starts = {}
        for i, r in enumerate(tec_rows):
            if r.phase == "settle" and r.settle_attempt and r.settle_attempt > 1:
                starts.setdefault(r.settle_attempt, x(r, i))
        for attempt, start in starts.items():
            ax.axvline(start, color=MUTED, linewidth=1, linestyle="--")
            ax.text(start, ax.get_ylim()[1], f" settle attempt {attempt}", color=TEXT_2, fontsize=8, va="top")

    ax.set_xlabel("time since start of run (s)", color=TEXT_2, fontsize=9)
    ax.set_ylabel("temperature (°C)", color=TEXT_2, fontsize=9)
    handles, _ = ax.get_legend_handles_labels()
    handles += [Patch(color=BAND, label=f"target {condition.setpoint_c:g} ± {tol:g} °C"),
                Patch(color=GRID, label="optical measurements taken")]
    ax.legend(handles=handles, loc="lower right", frameon=False, fontsize=8, labelcolor=TEXT_2)
    _title(ax, f"Temperature, {cond.label}: verification {cond.temp_result}",
           "Must stay inside the band and steady before light is measured; "
           "readings continue while measuring.")
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)


# --------------------------------------------------------------------------- 3. status grid


def grid_category(final: RawRow | None, retried: bool) -> int:
    """Index into GRID_CATEGORIES for one planned reading."""
    if final is None or final.status is Status.NOT_MEASURED:
        return 4
    if final.status is Status.VALID:
        return 1 if retried else 0
    if final.status is Status.INVALID:
        return 3
    return 2  # TIMEOUT or GARBLED after all retries


def status_matrix(all_rows: list[RawRow], finals: list[RawRow], cond: ConditionResult,
                  readings: int) -> list[list[int]]:
    """One row per channel, one column per planned reading, each a GRID_CATEGORIES index."""
    final_by_key = {(r.channel, r.measurement_index): r for r in finals
                    if r.condition == cond.label and r.phase == "measure"}
    retried = {(r.channel, r.measurement_index) for r in all_rows
               if r.condition == cond.label and r.phase == "measure" and r.attempt > 1}
    return [
        [grid_category(final_by_key.get((ch.channel, i)), (ch.channel, i) in retried)
         for i in range(1, readings + 1)]
        for ch in cond.channels
    ]


def plot_status(path: Path, all_rows: list[RawRow], finals: list[RawRow], cond: ConditionResult,
                config: AppConfig) -> None:
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    readings = config.sequence.measurement.readings_per_channel
    matrix = status_matrix(all_rows, finals, cond, readings)
    colours = [c for _, c in GRID_CATEGORIES]
    fig, ax = _figure(max(8.0, readings * 0.17 + 2.5), max(3.2, 0.32 * len(cond.channels) + 1.8))
    ax.grid(False)
    ax.pcolormesh(matrix, cmap=ListedColormap(colours),
                  norm=BoundaryNorm([i - 0.5 for i in range(len(colours) + 1)], len(colours)),
                  edgecolors=SURFACE, linewidth=1.5)
    ax.set_yticks([i + 0.5 for i in range(len(cond.channels))])
    ax.set_yticklabels([f"ch {ch.channel}" for ch in cond.channels], fontsize=8)
    ax.invert_yaxis()
    ticks = [i for i in range(1, readings + 1) if i == 1 or i % 10 == 0]
    ax.set_xticks([i - 0.5 for i in ticks])
    ax.set_xticklabels([str(i) for i in ticks], fontsize=8)
    ax.set_xlabel("reading number", color=TEXT_2, fontsize=9)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)

    counts = [sum(row.count(i) for row in matrix) for i in range(len(GRID_CATEGORIES))]
    ax.legend(
        handles=[Patch(color=c, label=f"{label} ({n})") for (label, c), n in zip(GRID_CATEGORIES, counts)],
        loc="upper center", bbox_to_anchor=(0.5, -0.22 if len(cond.channels) <= 8 else -0.12),
        ncol=len(GRID_CATEGORIES), frameon=False, fontsize=8, labelcolor=TEXT_2,
    )
    total = len(cond.channels) * readings
    _title(ax, f"What happened to each reading, {cond.label}",
           f"{counts[0] + counts[1]} of {total} readings valid; {counts[1]} needed a retry. "
           "A whole column = one message lost and re-asked; a single square = one channel re-read.")
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)


# --------------------------------------------------------------------------- all of them


def write_plots(run_dir: Path, rows: list[RawRow], summary: RunSummary, config: AppConfig,
                suffix: str = "") -> list[Path]:
    """Draw every chart for a run into its folder. Returns the files written.

    ``suffix`` keeps re-processed charts (new limits) apart from the originals, which are never overwritten.
    """
    run_dir = Path(run_dir)
    finals = final_attempts(rows)
    conditions = {c.label: c for c in config.sequence.conditions}
    written = []
    for cond in summary.conditions:
        name = _safe(cond.label) + suffix
        jobs = [
            (f"power_{name}.png", lambda p: plot_power(p, finals, cond, config)),
            (f"temperature_{name}.png", lambda p: plot_temperature(p, finals, cond, conditions[cond.label], config)),
            (f"status_{name}.png", lambda p: plot_status(p, rows, finals, cond, config)),
        ]
        for filename, draw in jobs:
            draw(run_dir / filename)
            written.append(run_dir / filename)
    return written


def plot_run(run_dir: Path, config_path: Path | None = None, suffix: str = "") -> list[Path]:
    """Draw the charts for a saved run: with the config it was run with (``ate plot``), or with new
    limits from another config (``ate reprocess --config``, which passes a suffix)."""
    from ate.config import load_config
    from ate.data.analysis import analyze, load_raw_rows
    from ate.data.report import CONFIG_FILE, RAW_FILE, RUN_INFO_FILE, read_json

    run_dir = Path(run_dir)
    raw_path = run_dir / RAW_FILE
    if not raw_path.is_file():
        raise FileNotFoundError(f"no {RAW_FILE} in {run_dir}")
    config = load_config(config_path or run_dir / CONFIG_FILE)
    info = read_json(run_dir / RUN_INFO_FILE) if (run_dir / RUN_INFO_FILE).is_file() else {}
    rows = load_raw_rows(raw_path)
    summary = analyze(rows, config, info.get("run_id", run_dir.name),
                      info.get("device_serial", config.run.device_serial))
    return write_plots(run_dir, rows, summary, config, suffix)
