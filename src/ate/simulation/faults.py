"""Fault injection: decides, per query, whether something goes wrong.

Faults are random (at the configured rates) or *forced* by an automated test through the bench's
control port, so every error path can be exercised on purpose.
"""

from __future__ import annotations

import random

from ate.core.settings import FaultRates

FAULT_KINDS = ("timeout", "garbled", "invalid", "channel_garbled", "channel_invalid")

_NOISE_CHARS = "#?~@!$%^&*"


class FaultInjector:
    def __init__(self, rng: random.Random, rates: FaultRates):
        self._rng = rng
        self.rates = rates
        self._forced: list[tuple[str, int | None]] = []

    def force(self, kind: str, channel: int | None = None) -> None:
        """Make the next matching roll fail, regardless of the rate."""
        if kind not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind '{kind}' (known: {', '.join(FAULT_KINDS)})")
        self._forced.append((kind, channel))

    def roll(self, kind: str, channel: int | None = None) -> bool:
        draw = self._rng.random()  # always drawn, so forcing a fault does not shift later rolls
        for index, (forced_kind, forced_channel) in enumerate(self._forced):
            if forced_kind == kind and (forced_channel is None or forced_channel == channel):
                del self._forced[index]
                return True
        return draw < getattr(self.rates, f"{kind}_rate")


def garble_value(text: str, rng: random.Random) -> str:
    """Corrupt a value so it can no longer be read as a number (like a bit flip on the wire)."""
    if not text:
        return rng.choice(_NOISE_CHARS)
    position = rng.randrange(len(text))
    return text[:position] + rng.choice(_NOISE_CHARS) + text[position + 1:]


def garble_reply(rng: random.Random) -> str:
    """A reply damaged beyond recognition: no channel labels survive."""
    return "".join(rng.choice(_NOISE_CHARS) for _ in range(rng.randint(4, 16)))
