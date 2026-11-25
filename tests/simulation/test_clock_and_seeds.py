"""ate/simulation/clock.py and rng.py: simulated time, and reproducible randomness."""

from __future__ import annotations

import pytest

from ate.simulation.clock import SimClock
from ate.simulation.rng import make_rng


def test_clock_only_moves_when_told():
    clock = SimClock()
    assert clock.now == 0
    clock.advance(1.5)
    clock.advance(0.25)
    assert clock.now == 1.75


def test_clock_cannot_run_backwards():
    with pytest.raises(ValueError):
        SimClock().advance(-1)


def test_same_seed_and_purpose_same_numbers():
    a = [make_rng(42, "opm_a", "input3").random() for _ in range(3)]
    assert a == [make_rng(42, "opm_a", "input3").random() for _ in range(3)]


def test_each_purpose_gets_independent_numbers():
    first = make_rng(42, "opm_a", "input3")
    second = make_rng(42, "opm_a", "input4")
    assert [first.random() for _ in range(5)] != [second.random() for _ in range(5)]


def test_different_seed_different_numbers():
    assert make_rng(42, "x").random() != make_rng(43, "x").random()
