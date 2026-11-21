"""Detection of a control loop that stopped completing."""
from __future__ import annotations

from typing import Optional

from .const import CONTROL_LOOP_STALL_SECONDS


class ControlWatchdog:
    """Track when the control loop last completed a run."""

    def __init__(self) -> None:
        self._completed_at: Optional[float] = None

    def completed(self, now: float) -> None:
        """Remember that a run completed."""
        self._completed_at = now

    def check(self, now: float) -> bool:
        """Return whether no run completed for too long, counting from the first check."""
        if self._completed_at is None:
            self._completed_at = now

        return now - self._completed_at >= CONTROL_LOOP_STALL_SECONDS
