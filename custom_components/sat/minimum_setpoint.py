import logging

_LOGGER = logging.getLogger(__name__)
ADJUSTMENT_FACTOR = 0.5


class MinimumSetpoint:
    def __init__(self):
        """Initialize the MinimumSetpoint class."""
        self._current = None

    def calculate(self, target_setpoint: float) -> float:
        """Adjust the minimum setpoint based on the requested setpoint and boiler temperature."""
        if self._current is None:
            self._current = target_setpoint

        old_value = self._current

        if self._current < target_setpoint:
            self._current = min(self._current + ADJUSTMENT_FACTOR, target_setpoint)
        else:
            self._current = max(self._current - ADJUSTMENT_FACTOR, target_setpoint)

        _LOGGER.info(
            "Minimum setpoint changed (%.1f°C => %.1f°C). Target Setpoint: %.1f°C",
            old_value, self._current, target_setpoint
        )

        return self._current
