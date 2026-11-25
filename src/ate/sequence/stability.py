"""Temperature stability rule, shared by the live test and by the after-the-fact analysis.

Because both use this one class, the summary can never disagree with what the test decided.
"""

from __future__ import annotations

import math
from collections import deque
from statistics import fmean

from ate.data.models import Status


class StabilityTracker:
    """Stable = the last N readings (spanning ``stable_for_s`` at ``poll_interval_s``) are all:

    * VALID,
    * within ``tolerance_c`` of the setpoint, and
    * no more than ``max_drift_c`` apart (max - min), i.e. the temperature has stopped moving.

    The last rule matters: a controller still heating up passes *through* the tolerance band and
    can spend several seconds inside it without being settled.
    An invalid or failed reading clears the window: it cannot confirm stability.
    """

    def __init__(self, setpoint_c: float, tolerance_c: float, poll_interval_s: float, stable_for_s: float,
                 max_drift_c: float = math.inf):
        self.setpoint_c = setpoint_c
        self.tolerance_c = tolerance_c
        self.max_drift_c = max_drift_c
        self.required = math.floor(stable_for_s / poll_interval_s + 1e-9) + 1
        self._window: deque[float] = deque(maxlen=self.required)
        self.stable = False

    def add(self, status: Status, value: float | None) -> bool:
        if self.stable:
            return True
        if status is Status.VALID and value is not None and abs(value - self.setpoint_c) <= self.tolerance_c:
            self._window.append(value)
        else:
            self._window.clear()
        if len(self._window) == self.required and max(self._window) - min(self._window) <= self.max_drift_c:
            self.stable = True
        return self.stable

    @property
    def window_mean(self) -> float | None:
        return fmean(self._window) if self._window else None
