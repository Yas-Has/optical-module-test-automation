"""ate/simulation/simulator.py: behaviour every simulated instrument shares."""

from __future__ import annotations

import random

from ate.core.settings import FaultRates
from ate.simulation.clock import SimClock
from ate.simulation.faults import FaultInjector
from ate.simulation.simulator import InstrumentSimulator, SimReply


class Dummy(InstrumentSimulator):
    """Answers PING, and READ? as a measurement query."""

    def _dispatch(self, command, argument):
        if command == "PING":
            return SimReply("PONG")
        if command == "READ?":
            self._begin_measurement()
            return SimReply(f"reading {self.measurement_queries}")
        return SimReply("ERR:UNKNOWN_COMMAND")


def dummy():
    return Dummy("dummy", SimClock(), FaultInjector(random.Random(0), FaultRates()))


def test_commands_are_case_insensitive_and_trimmed():
    assert dummy().handle("  ping \n").text == "PONG"


def test_empty_command():
    assert dummy().handle("   ").text == "ERR:EMPTY_COMMAND"


def test_kill_now():
    sim = dummy()
    sim.kill()
    assert sim.handle("PING").killed and not sim.alive


def test_kill_after_n_measurement_queries():
    sim = dummy()
    sim.kill(after_queries=2)
    assert sim.handle("PING").text == "PONG"  # setup commands don't count
    assert sim.handle("READ?").text == "reading 1"
    assert sim.handle("READ?").text == "reading 2"
    assert sim.handle("READ?").killed
    assert sim.handle("PING").killed  # stays dead
