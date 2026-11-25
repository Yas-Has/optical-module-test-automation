"""Simulated TEC.

Temperature approaches the setpoint exponentially (like a real thermal mass):

    T(t) = target + (T_start - target) * exp(-t / time_constant)

and each reading adds a small jitter. ``steady_state_error_c`` simulates a controller that settles
in the wrong place, which no amount of retrying can fix.
"""

from __future__ import annotations

import math

from ate.instruments.tec.commands import TecCommands
from ate.instruments.tec.settings import TecSettings
from ate.simulation.clock import SimClock
from ate.simulation.device import VirtualDevice
from ate.simulation.faults import FaultInjector, garble_value
from ate.simulation.rng import make_rng
from ate.simulation.simulator import InstrumentSimulator, SimReply


class TecSimulator(InstrumentSimulator):
    def __init__(self, config: TecSettings, device: VirtualDevice, clock: SimClock, seed: int):
        super().__init__(config.name, clock, FaultInjector(make_rng(seed, config.name, "faults"),
                                                           config.simulation.faults))
        self.settings = config.simulation
        self._noise = make_rng(seed, config.name, "noise")
        self._fault_values = make_rng(seed, config.name, "fault-values")
        self.ambient_c = device.config.ambient_temp_c
        self.setpoint_c = self.ambient_c
        self.output_on = False
        self._start_c = self.ambient_c
        self._start_time = clock.now
        device.attach_tec(self)  # the device link: the device's temperature follows this TEC

    # ----------------------------------------------------------------- thermal model

    def _target(self) -> float:
        return self.setpoint_c + self.settings.steady_state_error_c if self.output_on else self.ambient_c

    def true_temperature(self) -> float:
        tau = self.settings.time_constant_s
        target = self._target()
        if tau <= 0:
            return target
        elapsed = self.clock.now - self._start_time
        return target + (self._start_c - target) * math.exp(-elapsed / tau)

    def _change(self, *, setpoint: float | None = None, output_on: bool | None = None) -> None:
        # Restart the curve from wherever the temperature is right now.
        self._start_c = self.true_temperature()
        self._start_time = self.clock.now
        if setpoint is not None:
            self.setpoint_c = setpoint
        if output_on is not None:
            self.output_on = output_on

    # ----------------------------------------------------------------- commands

    def _dispatch(self, command: str, argument: str) -> SimReply:
        if command == TecCommands.IDENTIFY:
            return SimReply(f"XSIM,TEC-1000,{self.name},1.0")
        if command == TecCommands.RESET:
            self._change(setpoint=self.ambient_c, output_on=False)
            return SimReply("OK")
        if command == TecCommands.OUTPUT:
            if argument.upper() not in ("ON", "OFF"):
                return SimReply("ERR:BAD_VALUE")
            self._change(output_on=argument.upper() == "ON")
            return SimReply("OK")
        if command == TecCommands.OUTPUT_QUERY:
            return SimReply("ON" if self.output_on else "OFF")
        if command == TecCommands.SET_TEMPERATURE:
            try:
                self._change(setpoint=float(argument))
            except ValueError:
                return SimReply("ERR:BAD_VALUE")
            return SimReply("OK")
        if command == TecCommands.SETPOINT_QUERY:
            return SimReply(f"{self.setpoint_c:.3f}")
        if command == TecCommands.READ_TEMPERATURE:
            return self._read()
        return SimReply("ERR:UNKNOWN_COMMAND")

    def _read(self) -> SimReply:
        self._begin_measurement()
        # The value is always computed first, so fault decisions never shift the noise sequence.
        text = f"{self.true_temperature() + self._noise.gauss(0.0, self.settings.jitter_c):.3f}"
        if self.faults.roll("invalid"):
            text = self._fault_values.choice(["ERR:SENSOR_FAULT", "999.000"])
        if self.faults.roll("timeout"):
            return SimReply(None)
        if self.faults.roll("garbled"):
            return SimReply(garble_value(text, self._fault_values))
        return SimReply(text)
