"""How the test program waits.

With real hardware, waiting is just sleeping. With the simulator, every wait also moves the
bench's simulated clock forward, so the device "experiences" the time passing, and ``time_scale``
decides how much of that is actually slept for real (0 = instant).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ate.simulation.clock import SimClock
from ate.simulation.control import ControlClient


class RunClock(ABC):
    @abstractmethod
    def wait(self, seconds: float) -> None:
        """Let time pass."""

    def account(self, seconds: float) -> None:
        """Time that already passed in real life (e.g. a timeout); tell the simulator, don't sleep."""

    def elapsed(self) -> float | None:
        """Simulated seconds since start, or None with real hardware."""
        return None


class WallClock(RunClock):
    def wait(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class InProcessSimClock(RunClock):
    def __init__(self, clock: SimClock, time_scale: float):
        self._clock = clock
        self._scale = time_scale

    def wait(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self._clock.advance(seconds)
        if self._scale:
            time.sleep(seconds * self._scale)

    def account(self, seconds: float) -> None:
        if seconds > 0:
            self._clock.advance(seconds)

    def elapsed(self) -> float | None:
        return self._clock.now


class NetworkSimClock(RunClock):
    def __init__(self, control: ControlClient, time_scale: float):
        self._control = control
        self._scale = time_scale

    def wait(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self._control.send(f"SIM:ADVANCE {seconds:.6f}")
        if self._scale:
            time.sleep(seconds * self._scale)

    def account(self, seconds: float) -> None:
        if seconds > 0:
            self._control.send(f"SIM:ADVANCE {seconds:.6f}")

    def elapsed(self) -> float | None:
        return float(self._control.send("SIM:TIME?"))
