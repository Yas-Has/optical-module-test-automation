"""What every simulated instrument has in common. Each instrument's own simulator
lives in its folder (ate/instruments/<name>/simulator.py) and extends this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ate.simulation.clock import SimClock
from ate.simulation.faults import FaultInjector


@dataclass(frozen=True)
class SimReply:
    """How a simulator answers one command.

    text     the reply line, or None to stay silent (a real timeout)
    delay_s  how long the measurement takes in simulated time (slept for real x time_scale)
    killed   the instrument has "died": the connection must be dropped
    """

    text: str | None
    delay_s: float = 0.0
    killed: bool = False


class _Killed(Exception):
    pass


class InstrumentSimulator(ABC):
    def __init__(self, name: str, clock: SimClock, faults: FaultInjector):
        self.name = name
        self.clock = clock
        self.faults = faults
        self.measurement_queries = 0
        self._killed = False
        self._kill_after: int | None = None

    def kill(self, after_queries: int | None = None) -> None:
        """Simulate the instrument dying, now or after N more measurement queries."""
        if after_queries is None:
            self._killed = True
        else:
            self._kill_after = self.measurement_queries + after_queries

    @property
    def alive(self) -> bool:
        return not self._killed

    def handle(self, line: str) -> SimReply:
        """Process one command line. Never raises: problems become error replies."""
        if self._killed:
            return SimReply(None, killed=True)
        parts = line.strip().split(None, 1)
        if not parts:
            return SimReply("ERR:EMPTY_COMMAND")
        command, argument = parts[0].upper(), (parts[1].strip() if len(parts) > 1 else "")
        try:
            return self._dispatch(command, argument)
        except _Killed:
            return SimReply(None, killed=True)

    def _begin_measurement(self) -> None:
        """Call at the start of every measurement query (counts it, and applies a scheduled kill)."""
        self.measurement_queries += 1
        if self._kill_after is not None and self.measurement_queries > self._kill_after:
            self._killed = True
            raise _Killed

    @abstractmethod
    def _dispatch(self, command: str, argument: str) -> SimReply: ...
