from __future__ import annotations

from typing import Optional

from .const import BOILER_MODULATION_DELTA_THRESHOLD, BOILER_MODULATION_RELIABILITY_MIN_SAMPLES
from .types import DeviceState


class ModulationReliabilityTracker:
    def __init__(self) -> None:
        self._reliable: Optional[bool] = None
        self._values_when_flame_on: list[float] = []

    @property
    def reliable(self) -> Optional[bool]:
        return self._reliable

    def load(self, reliable: Optional[bool]) -> None:
        self._reliable = reliable

    def update(self, state: DeviceState) -> bool:
        """Track whether modulation readings show sustained, meaningful variation."""
        if not state.flame_active:
            return False

        max_modulation = state.max_modulation_level
        current_modulation = state.relative_modulation_level
        if current_modulation is None or max_modulation is None or max_modulation < BOILER_MODULATION_DELTA_THRESHOLD:
            return False

        self._values_when_flame_on.append(current_modulation)

        if len(self._values_when_flame_on) > 50:
            self._values_when_flame_on = self._values_when_flame_on[-50:]

        if len(self._values_when_flame_on) < BOILER_MODULATION_RELIABILITY_MIN_SAMPLES:
            return False

        window = self._values_when_flame_on[-BOILER_MODULATION_RELIABILITY_MIN_SAMPLES:]
        above_threshold = sum(1 for value in window if value >= BOILER_MODULATION_DELTA_THRESHOLD)
        required_samples = max(2, int(len(window) * 0.4))

        previous = self._reliable
        self._reliable = above_threshold >= required_samples

        return previous != self._reliable
