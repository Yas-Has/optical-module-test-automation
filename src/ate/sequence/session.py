"""Sets up the instruments for a run: real hardware, a simulator bench in the background,
or simulators inside this process. Always tears everything down again.

This is the *only* place that knows which of the three it is. The test runner just gets
drivers and a clock.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ate.config import AppConfig
from ate.core.settings import InstrumentConfigBase
from ate.core import registry
from ate.core.instrument import Instrument, TransportFactory
from ate.core.roles import PowerMeter, TemperatureController
from ate.core.transport import VisaTransport
from ate.sequence.clock import InProcessSimClock, NetworkSimClock, RunClock, WallClock
from ate.simulation.bench import Bench
from ate.simulation.control import (
    BenchProcess,
    ControlClient,
    NetworkBenchControl,
    assign_ports,
    instrument_address,
)
from ate.simulation.loopback import LoopbackTransport

log = logging.getLogger("ate.session")

BenchHook = Callable[[Any], None]  # receives Bench (in-process) or NetworkBenchControl


class InstrumentSession:
    def __init__(self, config: AppConfig, *, seed: int, config_path: Path, run_dir: Path,
                 external_bench: bool = False, bench_hook: BenchHook | None = None):
        self.config = config
        self.seed = seed
        self.config_path = config_path
        self.run_dir = run_dir
        self.external_bench = external_bench
        self.bench_hook = bench_hook
        self.clock: RunClock = WallClock()
        self.instruments: list[Instrument] = []
        self.bench: Bench | None = None
        self._bench_process: BenchProcess | None = None
        self._control: ControlClient | None = None
        self.addresses: dict[str, str] = {}

    # ----------------------------------------------------------------- set up

    def __enter__(self) -> InstrumentSession:
        try:
            self._open()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _open(self) -> None:
        sim = self.config.simulation
        if not sim.enabled:
            log.info("using real hardware")
            self.clock = WallClock()
            self.addresses = {i.name: i.address for i in self.config.instruments}
            factory = self._visa_factory
        elif sim.mode == "in_process":
            log.info("using in-process simulators (seed %d)", self.seed)
            self.bench = Bench(self.config, self.seed)
            if self.bench_hook:
                self.bench_hook(self.bench)
            self.clock = InProcessSimClock(self.bench.clock, sim.time_scale)
            self.addresses = {name: "in-process" for name in self.bench.simulators}
            factory = self._loopback_factory
        else:
            if not self.external_bench:
                self._bench_process = BenchProcess(self.config, self.config_path, self.run_dir / "bench.log")
                self._bench_process.start()
            self._control = ControlClient(sim.host, sim.control_port)
            if self.bench_hook:
                self.bench_hook(NetworkBenchControl(self._control))
            self.clock = NetworkSimClock(self._control, sim.time_scale)
            ports = assign_ports(self.config)
            self.addresses = {i.name: instrument_address(self.config, i.name, ports) for i in self.config.instruments}
            factory = self._visa_factory
        self.instruments = [
            registry.get(inst.type).driver(inst, factory(inst)) for inst in self.config.instruments
        ]

    def _visa_factory(self, inst: InstrumentConfigBase) -> TransportFactory:
        return lambda: VisaTransport(self.addresses[inst.name], inst.timeout_ms)

    def _loopback_factory(self, inst: InstrumentConfigBase) -> TransportFactory:
        return lambda: LoopbackTransport(self.bench.simulator(inst.name), self.config.simulation.time_scale)

    def connect_all(self) -> list[dict]:
        """Connect and configure every instrument. Returns their details for run_info.json."""
        details = []
        for instrument in self.instruments:
            instrument.connect()
            instrument.configure(self.config)
            details.append({
                "name": instrument.name,
                "type": instrument.type_name,
                "role": instrument.role,
                "address": self.addresses.get(instrument.name),
                "identity": instrument.identity,
            })
        return details

    @property
    def tecs(self) -> list[TemperatureController]:
        return [i for i in self.instruments if isinstance(i, TemperatureController)]

    @property
    def meters(self) -> list[PowerMeter]:
        return [i for i in self.instruments if isinstance(i, PowerMeter)]

    # ----------------------------------------------------------------- tear down (always)

    def __exit__(self, *exc_info) -> None:
        for instrument in reversed(self.instruments):
            instrument.close()  # never raises; logs its own problems
        if self._bench_process is not None:
            self._bench_process.stop(self._control)
            self._bench_process = None
        if self._control is not None:
            self._control.close()
            self._control = None
