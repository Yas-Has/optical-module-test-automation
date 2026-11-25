"""The contract every instrument driver follows, simulated or real, plus how a reply is judged."""

from __future__ import annotations

import logging
import math
from abc import ABC
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from ate.core.transport import CommTimeout, Transport
from ate.data.models import Status

if TYPE_CHECKING:
    from ate.config import AppConfig
    from ate.core.settings import InstrumentConfigBase

log = logging.getLogger("ate.instruments")

IDENTIFY = "*IDN?"  # standard command almost every lab instrument understands

TransportFactory = Callable[[], Transport]


class InstrumentError(Exception):
    """A setup command failed. The test cannot continue without this instrument."""


def classify_value(text: str | None, valid_range: tuple[float, float]) -> tuple[Status, float | None]:
    """Decide what a single reply value means.

    no reply                              -> TIMEOUT
    "ERR..."                              -> INVALID (instrument reported a problem)
    not a number                          -> GARBLED (damaged in transit)
    NaN / infinite / outside the range    -> INVALID (physically impossible; value kept)
    anything else                         -> VALID (even if it fails the test limits)
    """
    if text is None:
        return Status.TIMEOUT, None
    cleaned = text.strip()
    if cleaned.upper().startswith("ERR"):
        return Status.INVALID, None
    try:
        value = float(cleaned)
    except ValueError:
        return Status.GARBLED, None
    if not math.isfinite(value):
        return Status.INVALID, None
    low, high = valid_range
    if not low <= value <= high:
        return Status.INVALID, value
    return Status.VALID, value


class Instrument(ABC):
    """Common lifecycle: connect -> configure -> (measure) -> close.

    Measurement methods never raise for a bad reading; they return a Measurement with a status.
    Exceptions are reserved for problems the test cannot continue past.
    """

    role: ClassVar[str] = ""

    def __init__(self, config: InstrumentConfigBase, transport_factory: TransportFactory):
        self.config = config
        self.name = config.name
        self.type_name = config.type
        self._transport_factory = transport_factory
        self._transport: Transport | None = None
        self.identity: str | None = None

    # ------------------------------------------------------------- lifecycle

    @property
    def connected(self) -> bool:
        return self._transport is not None

    @property
    def transport(self) -> Transport:
        if self._transport is None:
            raise InstrumentError(f"{self.name} is not connected")
        return self._transport

    def connect(self) -> None:
        if self.connected:
            return
        self._transport = self._transport_factory()
        try:
            self.identity = self.query_setup(IDENTIFY)
        except Exception:
            self._close_transport()
            raise
        log.info("%s connected: %s", self.name, self.identity)

    def configure(self, app_config: AppConfig) -> None:
        """Apply instrument settings. Override where needed."""

    def close(self) -> None:
        """Leave the instrument safe, then disconnect. Safe to call more than once."""
        if self._transport is None:
            return
        try:
            self._safe_shutdown()
        except Exception as exc:
            log.warning("%s: could not shut down cleanly: %s", self.name, exc)
        finally:
            self._close_transport()
            log.info("%s disconnected", self.name)

    def _safe_shutdown(self) -> None:
        """Put the instrument in a safe state before disconnecting. Override where needed."""

    def _close_transport(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:
                log.warning("%s: error while disconnecting: %s", self.name, exc)

    def clear(self) -> None:
        try:
            self.transport.clear()
        except Exception:
            pass

    def __enter__(self) -> Instrument:
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------- communication helpers

    def query_setup(self, command: str) -> str:
        """Setup/identity query: retried on timeout, raises InstrumentError if it never answers."""
        attempts = self.config.max_comm_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return self.transport.query(command).strip()
            except CommTimeout:
                log.warning("%s: no reply to '%s' (attempt %d/%d)", self.name, command, attempt, attempts)
                self.clear()
        raise InstrumentError(f"{self.name} did not answer '{command}' after {attempts} attempts")

    def send_setup(self, command: str) -> None:
        reply = self.query_setup(command)
        if reply.upper() != "OK":
            raise InstrumentError(f"{self.name} rejected '{command}': {reply}")

    def _measure_query(self, command: str) -> str | None:
        """Measurement query: one attempt only. Returns None on timeout (the runner decides on retries)."""
        try:
            return self.transport.query(command).strip()
        except CommTimeout:
            return None
