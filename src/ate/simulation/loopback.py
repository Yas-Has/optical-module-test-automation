"""In-process transport: talks to a simulator directly, no network. Used for fast automated tests.

Drivers cannot tell the difference: a silent simulator still produces a CommTimeout,
a dead one still produces ConnectionLost.
"""

from __future__ import annotations

import time

from ate.core.transport import CommTimeout, ConnectionLost, Transport
from ate.simulation.simulator import InstrumentSimulator


class LoopbackTransport(Transport):
    def __init__(self, simulator: InstrumentSimulator, time_scale: float = 0.0):
        self.simulator = simulator
        self.time_scale = time_scale
        self._open = True

    def query(self, command: str) -> str:
        if not self._open:
            raise ConnectionLost(f"{self.simulator.name}: connection is closed")
        reply = self.simulator.handle(command)
        if reply.killed:
            self._open = False
            raise ConnectionLost(f"{self.simulator.name} stopped responding (connection lost)")
        if reply.delay_s and self.time_scale:
            time.sleep(reply.delay_s * self.time_scale)
        if reply.text is None:
            raise CommTimeout(f"no reply to '{command}' from {self.simulator.name}")
        return reply.text

    def close(self) -> None:
        self._open = False
