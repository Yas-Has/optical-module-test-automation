"""Simulated time.

Settling, drift and averaging all follow this clock instead of the real one. That is what makes
runs reproducible from a seed, and lets a 10-minute test finish in a second with identical data.
"""

from __future__ import annotations


class SimClock:
    def __init__(self) -> None:
        self._now = 0.0

    @property
    def now(self) -> float:
        """Simulated seconds since the bench started."""
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("time cannot run backwards")
        self._now += seconds
