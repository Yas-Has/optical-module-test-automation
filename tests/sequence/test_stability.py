"""ate/sequence/stability.py: when is the temperature "verified"?"""

from __future__ import annotations

import math

import pytest

from ate.data.models import Status
from ate.sequence.stability import StabilityTracker


def test_window_length_covers_stable_for_seconds():
    assert StabilityTracker(40, 0.5, poll_interval_s=1.0, stable_for_s=10.0).required == 11
    assert StabilityTracker(40, 0.5, poll_interval_s=0.5, stable_for_s=3.0).required == 7
    assert StabilityTracker(40, 0.5, poll_interval_s=1.0, stable_for_s=0.0).required == 1


def test_needs_an_unbroken_window():
    tracker = StabilityTracker(40.0, 0.5, poll_interval_s=1.0, stable_for_s=3.0)  # needs 4 readings
    for value in (40.1, 40.0, 39.9):
        assert not tracker.add(Status.VALID, value)
    assert not tracker.add(Status.INVALID, None)  # an invalid reading cannot confirm stability: restart
    for value in (40.0, 40.0, 40.0):
        assert not tracker.add(Status.VALID, value)
    assert tracker.add(Status.VALID, 40.2)
    assert tracker.window_mean == pytest.approx(40.05)


def test_out_of_tolerance_restarts_the_window():
    tracker = StabilityTracker(40.0, 0.5, poll_interval_s=1.0, stable_for_s=1.0)
    assert not tracker.add(Status.VALID, 40.0)
    assert not tracker.add(Status.VALID, 40.6)
    assert not tracker.add(Status.VALID, 40.0)
    assert tracker.add(Status.VALID, 40.0)


def test_still_rising_through_the_band_is_not_stable():
    # Heating from 25 C towards 40.8 C passes through 39.5-40.5 C for ~11 s. Inside tolerance,
    # but still moving, so it must not count as settled.
    tracker = StabilityTracker(40.0, 0.5, poll_interval_s=1.0, stable_for_s=10.0, max_drift_c=0.1)
    rising = [40.8 - 15.8 * math.exp(-t / 8) for t in range(20, 32)]
    assert all(abs(v - 40) <= 0.5 for v in rising[:11])
    assert not any(tracker.add(Status.VALID, v) for v in rising)


def test_settled_with_small_wobble_is_stable():
    tracker = StabilityTracker(40.0, 0.5, poll_interval_s=1.0, stable_for_s=10.0, max_drift_c=0.1)
    wobble = [40.02, 39.98, 40.01, 40.03, 39.99, 40.00, 40.02, 39.97, 40.01, 40.00, 40.02]
    assert [tracker.add(Status.VALID, v) for v in wobble][-1]
