"""Simulated 8-input optical power meter.

reading = device power for that channel (includes the temperature effect) + noise

Noise shrinks with longer averaging, like a real meter:
    sigma = noise_db_at_100ms * sqrt(100 / averaging_time_ms)
"""

from __future__ import annotations

import math

from ate.instruments.opm.commands import OpmCommands
from ate.instruments.opm.settings import OpmSettings
from ate.simulation.clock import SimClock
from ate.simulation.device import VirtualDevice
from ate.simulation.faults import FaultInjector, garble_reply, garble_value
from ate.simulation.rng import make_rng
from ate.simulation.simulator import InstrumentSimulator, SimReply

DARK_LEVEL_DBM = -75.0  # what an input with no fibre attached reads
DEFAULT_AVERAGING_MS = 100.0


class OpmSimulator(InstrumentSimulator):
    def __init__(self, config: OpmSettings, device: VirtualDevice, clock: SimClock, seed: int):
        super().__init__(config.name, clock, FaultInjector(make_rng(seed, config.name, "faults"),
                                                           config.simulation.faults))
        self.device = device
        self.input_count = config.INPUTS
        self.wiring = {n: ch for n, ch in enumerate(config.device_channels, start=1)}  # input -> device channel
        self._noise = {n: make_rng(seed, config.name, f"input{n}", "noise") for n in range(1, self.input_count + 1)}
        self._fault_values = make_rng(seed, config.name, "fault-values")
        self.averaging_ms = DEFAULT_AVERAGING_MS

    # ----------------------------------------------------------------- measurement model

    def noise_sigma_db(self) -> float:
        return self.device.config.noise_db_at_100ms * math.sqrt(100.0 / self.averaging_ms)

    def _power(self, number: int) -> float:
        channel = self.wiring.get(number)
        base = DARK_LEVEL_DBM if channel is None else self.device.true_power_dbm(channel)
        return base + self._noise[number].gauss(0.0, self.noise_sigma_db())

    def _value_text(self, number: int) -> str:
        value = self._power(number)  # always drawn first so faults never shift the noise sequence
        if self.faults.roll("channel_invalid", number):
            return self._fault_values.choice(["ERR:SATURATED", "ERR:NO_SIGNAL", f"{45.0 + value:.3f}"])
        return f"{value:.3f}"

    def _take_time(self) -> float:
        seconds = self.averaging_ms / 1000.0
        self.clock.advance(seconds)
        return seconds

    # ----------------------------------------------------------------- commands

    def _dispatch(self, command: str, argument: str) -> SimReply:
        if command == OpmCommands.IDENTIFY:
            return SimReply(f"XSIM,OPM-{self.input_count},{self.name},1.0")
        if command == OpmCommands.RESET:
            self.averaging_ms = DEFAULT_AVERAGING_MS
            return SimReply("OK")
        if command == OpmCommands.UNIT:
            return SimReply("OK" if argument.upper() == "DBM" else "ERR:UNSUPPORTED_UNIT")
        if command == OpmCommands.AVERAGING:
            try:
                value = float(argument)
            except ValueError:
                return SimReply("ERR:BAD_VALUE")
            if value <= 0:
                return SimReply("ERR:BAD_VALUE")
            self.averaging_ms = value
            return SimReply("OK")
        if command == OpmCommands.AVERAGING_QUERY:
            return SimReply(f"{self.averaging_ms:g}")
        if command == OpmCommands.READ_ONE:
            try:
                number = int(argument)
            except ValueError:
                return SimReply("ERR:BAD_CHANNEL")
            if not 1 <= number <= self.input_count:
                return SimReply("ERR:BAD_CHANNEL")
            return self._read_one(number)
        if command == OpmCommands.READ_ALL:
            return self._read_all()
        return SimReply("ERR:UNKNOWN_COMMAND")

    def _read_one(self, number: int) -> SimReply:
        self._begin_measurement()
        delay = self._take_time()
        text = self._value_text(number)
        if self.faults.roll("channel_garbled", number):
            text = garble_value(text, self._fault_values)
        if self.faults.roll("timeout"):
            return SimReply(None, delay)
        if self.faults.roll("garbled"):
            return SimReply(garble_value(text, self._fault_values), delay)
        return SimReply(text, delay)

    def _read_all(self) -> SimReply:
        self._begin_measurement()
        delay = self._take_time()  # every input measured during the same window
        fields = []
        for number in range(1, self.input_count + 1):
            text = self._value_text(number)
            if self.faults.roll("channel_garbled", number):
                form = self._fault_values.randrange(3)
                if form == 0:
                    fields.append(f"{number}:{garble_value(text, self._fault_values)}")  # value damaged
                elif form == 1:
                    fields.append(f"{number}:")  # value lost
                # form 2: the whole field is lost
            else:
                fields.append(f"{number}:{text}")
        if self.faults.roll("timeout"):
            return SimReply(None, delay)
        if self.faults.roll("garbled"):
            return SimReply(garble_reply(self._fault_values), delay)
        return SimReply(",".join(fields), delay)
