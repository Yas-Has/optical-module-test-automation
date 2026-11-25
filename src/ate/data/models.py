"""The basic record every reading is stored as."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum


class Status(StrEnum):
    """What happened to one attempt at one reading.

    VALID         a sensible value (it may still fail the test limits; that is real data)
    INVALID       the instrument answered clearly, but with an impossible value or an error code
    TIMEOUT       no answer at all (communication problem, retryable)
    GARBLED       an answer arrived but could not be understood (communication problem, retryable)
    NOT_MEASURED  a planned reading that was never attempted (instrument lost / run aborted)
    """

    VALID = "VALID"
    INVALID = "INVALID"
    TIMEOUT = "TIMEOUT"
    GARBLED = "GARBLED"
    NOT_MEASURED = "NOT_MEASURED"

    @property
    def retryable(self) -> bool:
        return self in (Status.TIMEOUT, Status.GARBLED)


def utc_now_iso() -> str:
    """Current UTC time, e.g. 2026-11-23T14:30:07.512Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Measurement:
    """One attempt at one reading, exactly as it will appear in the raw CSV.

    Drivers fill in what they know (instrument, value, status, raw reply).
    The test runner adds the context (condition, phase, index, attempt).
    """

    instrument: str
    instrument_type: str
    unit: str
    status: Status
    value: float | None = None
    raw_response: str | None = None
    channel: int | None = None
    instrument_input: int | None = None
    attempt: int = 1
    timestamp_utc: str = field(default_factory=utc_now_iso)
    sim_elapsed_s: float | None = None
    condition: str = ""
    phase: str = ""
    measurement_index: int | None = None
    settle_attempt: int | None = None
    note: str = ""

    def with_context(self, **changes) -> Measurement:
        return replace(self, **changes)
