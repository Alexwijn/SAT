import logging

_LOGGER = logging.getLogger(__name__)
ADJUSTMENT_STEP = 0.5  # Renamed for clarity


class SetpointAdjuster:
    def __init__(self):
        """Initialize the SetpointAdjuster with no current setpoint."""
        self._current = None

    @property
    def current(self) -> float:
        """Return the current setpoint."""
        return self._current

    def adjust(self, target_setpoint: float) -> float:
        """Gradually adjust the current setpoint toward the target setpoint."""
        if self._current is None:
            self._current = target_setpoint

        previous_setpoint = self._current

        if self._current < target_setpoint:
            self._current = min(self._current + ADJUSTMENT_STEP, target_setpoint)
        elif self._current > target_setpoint:
            self._current = max(self._current - ADJUSTMENT_STEP, target_setpoint)

        _LOGGER.info(
            "Setpoint updated: %.1f°C -> %.1f°C (Target: %.1f°C)",
            previous_setpoint, self._current, target_setpoint
        )

        return self._current
