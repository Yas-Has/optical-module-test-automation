"""ate/simulation/faults.py: random and forced faults, and how damage is made."""

from __future__ import annotations

import random

import pytest

from ate.core.settings import FaultRates
from ate.simulation.faults import FaultInjector, garble_reply, garble_value


def injector(**rates):
    return FaultInjector(random.Random(0), FaultRates(**rates))


def test_rate_zero_never_and_rate_one_always():
    assert not any(injector().roll("timeout") for _ in range(1000))
    assert all(injector(timeout_rate=1.0).roll("timeout") for _ in range(1000))


def test_rate_is_respected_on_average():
    faults = injector(garbled_rate=0.1)
    hits = sum(faults.roll("garbled") for _ in range(20000))
    assert hits / 20000 == pytest.approx(0.1, abs=0.01)


def test_forced_fault_happens_exactly_once():
    faults = injector()
    faults.force("timeout")
    assert faults.roll("timeout") and not faults.roll("timeout")


def test_forced_channel_fault_only_matches_that_channel():
    faults = injector()
    faults.force("channel_invalid", 3)
    assert not faults.roll("channel_invalid", 2)
    assert faults.roll("channel_invalid", 3)


def test_forcing_does_not_shift_later_random_rolls():
    plain, forced = injector(timeout_rate=0.5), injector(timeout_rate=0.5)
    forced.force("timeout")
    forced.roll("timeout")
    plain.roll("timeout")
    assert [plain.roll("timeout") for _ in range(50)] == [forced.roll("timeout") for _ in range(50)]


def test_unknown_fault_kind():
    with pytest.raises(ValueError, match="unknown fault kind"):
        injector().force("meteor_strike")


def test_garbled_values_can_never_be_mistaken_for_numbers():
    rng = random.Random(0)
    for _ in range(5000):
        with pytest.raises(ValueError):
            float(garble_value(f"{rng.uniform(-60, 60):.3f}", rng))


def test_garbled_reply_has_no_channel_labels():
    rng = random.Random(0)
    assert not any(":" in garble_reply(rng) or "\n" in garble_reply(rng) for _ in range(2000))
