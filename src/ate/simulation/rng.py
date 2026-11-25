"""Seeded random-number sequences.

One main seed produces many independent sequences, one per purpose, e.g. ``42:opm_a:input3:noise``.
So a retry on one channel cannot shift the numbers used by another, and fault decisions never
shift the noise values.
"""

from __future__ import annotations

import random


def make_rng(seed: int, *purpose: object) -> random.Random:
    # Seeding with a string is deterministic across runs and machines (hashed with SHA-512).
    return random.Random(":".join([str(seed), *map(str, purpose)]))
