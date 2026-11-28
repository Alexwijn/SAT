"""Hold, then release, while a signal that control depends on is missing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .const import FAIL_SAFE_GRACE_SECONDS, FAIL_SAFE_RESUME_SECONDS
from .helpers import sustained_runtime
from .types import ControlSignal, FailSafePhase


@dataclass(frozen=True, slots=True)
class FailSafeState:
    """Phase of the fail-safe and the signal it waits for."""

    phase: FailSafePhase
    previous: FailSafePhase
    missing: Optional[ControlSignal] = None

    @property
    def just_released(self) -> bool:
        """Return whether the grace period ran out just now."""
        return self.phase == FailSafePhase.RELEASED and self.previous != FailSafePhase.RELEASED

    @property
    def just_resumed(self) -> bool:
        """Return whether control comes back from a release just now."""
        return self.phase == FailSafePhase.CONTROLLING and self.previous == FailSafePhase.RELEASED


class FailSafeMonitor:
    """Track how long a signal is missing, when the boiler has to be released and when it is safe to take it back."""

    def __init__(self) -> None:
        self._missing_since: Optional[float] = None
        self._present_since: Optional[float] = None
        self._released_for: Optional[ControlSignal] = None
        self._state = FailSafeState(phase=FailSafePhase.CONTROLLING, previous=FailSafePhase.CONTROLLING)

    @property
    def state(self) -> FailSafeState:
        """Return the state of the last update."""
        return self._state

    def update(self, missing: Optional[ControlSignal], now: float, *, require_stable: bool) -> FailSafeState:
        """Advance the fail-safe, resuming from a release only after the signal has been stable."""
        phase = self._phase(missing, now, require_stable)
        self._released_for = missing or self._released_for if phase == FailSafePhase.RELEASED else None
        self._state = FailSafeState(phase=phase, previous=self._state.phase, missing=missing or self._released_for)

        return self._state

    def _phase(self, missing: Optional[ControlSignal], now: float, require_stable: bool) -> FailSafePhase:
        if missing is None:
            if require_stable and self._state.phase == FailSafePhase.RELEASED:
                runtime = sustained_runtime(now, self._present_since)
                self._present_since = runtime.started_at

                if runtime.elapsed_seconds < FAIL_SAFE_RESUME_SECONDS:
                    return FailSafePhase.RELEASED

            self._missing_since = None
            self._present_since = None
            return FailSafePhase.CONTROLLING

        self._present_since = None

        runtime = sustained_runtime(now, self._missing_since)
        self._missing_since = runtime.started_at

        return FailSafePhase.RELEASED if runtime.elapsed_seconds >= FAIL_SAFE_GRACE_SECONDS else FailSafePhase.HOLDING
