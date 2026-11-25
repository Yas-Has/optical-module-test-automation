"""Command line: ``ate run``, ``ate bench``, ``ate reprocess``, ``ate plot``, ``ate validate``.

Exit codes: 0 = device PASS (or command succeeded), 1 = device FAIL, 2 = run aborted or bad input.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

from ate.config import ConfigError, load_config

DEFAULT_CONFIG = "config/default.yaml"


def _overrides(args: argparse.Namespace) -> dict:
    overrides = {}
    if getattr(args, "seed", None) is not None:
        overrides["run.seed"] = args.seed
    if getattr(args, "serial", None):
        overrides["run.device_serial"] = args.serial
    if getattr(args, "results_dir", None):
        overrides["run.results_dir"] = args.results_dir
    if getattr(args, "mode", None):
        overrides["simulation.mode"] = args.mode
    if getattr(args, "time_scale", None) is not None:
        overrides["simulation.time_scale"] = args.time_scale
    return overrides


def cmd_run(args: argparse.Namespace) -> int:
    from ate.sequence.runner import ABORTED, TestRunner
    from ate.simulation.control import BenchError

    config = load_config(args.config, _overrides(args))
    try:
        result = TestRunner(config, external_bench=args.external_bench).run()
    except BenchError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 2
    if result.status == ABORTED:
        return 2
    return 0 if result.overall_result == "PASS" else 1


def cmd_bench(args: argparse.Namespace) -> int:
    from ate.simulation.bench import Bench
    from ate.simulation.control import check_ports_free

    config = load_config(args.config, _overrides(args))
    seed = config.run.seed if config.run.seed is not None else secrets.randbelow(2**31)
    if not args.skip_port_check:
        check_ports_free(config)
    Bench(config, seed).run(announce=lambda text: print(text, flush=True))
    return 0


def cmd_reprocess(args: argparse.Namespace) -> int:
    from ate.data.report import format_summary, reprocess_run

    summary, out = reprocess_run(Path(args.run_dir), Path(args.config) if args.config else None)
    print(format_summary(summary))
    print(f"\nSummary written to {out}")
    return 0 if summary.overall_result == "PASS" else 1


def cmd_plot(args: argparse.Namespace) -> int:
    from ate.data.plots import plot_run, plots_available

    if not plots_available():
        print('Charts need matplotlib: pip install ".[plots]"', file=sys.stderr)
        return 2
    for path in plot_run(Path(args.run_dir)):
        print(f"Saved {path}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_config(args.config, _overrides(args))
    print(f"{args.config} is valid.")
    print(f"  instruments: {', '.join(f'{i.name} ({i.type})' for i in config.instruments)}")
    print(f"  device channels: {config.simulation.device.channels}")
    print(f"  conditions: {', '.join(c.label for c in config.sequence.conditions)}")
    estimate = config.estimated_real_duration_s()
    print(f"  estimated real duration: {'instant' if estimate == 0 else f'{estimate:.0f} s'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ate", description="Optical module test automation (simulated instruments).")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default=DEFAULT_CONFIG, help=f"config file (default: {DEFAULT_CONFIG})")

    run = sub.add_parser("run", help="run the full test sequence")
    add_config(run)
    run.add_argument("--seed", type=int, help="random seed (replays a previous run exactly)")
    run.add_argument("--serial", help="device serial number")
    run.add_argument("--results-dir", help="where to create the run folder")
    run.add_argument("--mode", choices=["network", "in_process"], help="how to run the simulators")
    run.add_argument("--time-scale", type=float, help="1 = real time, 0 = instant")
    run.add_argument("--external-bench", action="store_true",
                     help="connect to a bench already started with `ate bench` instead of starting one")
    run.set_defaults(func=cmd_run)

    bench = sub.add_parser("bench", help="start the simulated instruments and leave them running")
    add_config(bench)
    bench.add_argument("--seed", type=int)
    bench.add_argument("--skip-port-check", action="store_true", help=argparse.SUPPRESS)
    bench.set_defaults(func=cmd_bench)

    reprocess = sub.add_parser("reprocess", help="re-analyse a saved run without re-testing")
    reprocess.add_argument("run_dir", help="the run's results folder")
    reprocess.add_argument("--config", help="use different limits (writes a new summary file)")
    reprocess.set_defaults(func=cmd_reprocess)

    plot = sub.add_parser("plot", help="draw the charts for a saved run")
    plot.add_argument("run_dir", help="the run's results folder")
    plot.set_defaults(func=cmd_plot)

    validate = sub.add_parser("validate", help="check a config file without running anything")
    add_config(validate)
    validate.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 2
