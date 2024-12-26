import logging

_LOGGER = logging.getLogger(__name__)
ADJUSTMENT_FACTOR = 0.5


class MinimumSetpoint:
    def __init__(self):
        """Initialize the MinimumSetpoint class."""
        self._current = None

    def calculate(self, target_setpoint: float, boiler_temperature: float) -> float:
        """Adjust the minimum setpoint based on the requested setpoint and boiler temperature."""
        if self._current is None:
            self._current = boiler_temperature

        old_value = self._current

        if self._current < boiler_temperature:
            self._current = min(self._current + ADJUSTMENT_FACTOR, boiler_temperature)
        else:
            self._current = max(self._current - ADJUSTMENT_FACTOR, boiler_temperature)

        _LOGGER.info(
            "Minimum setpoint changed (%.1f°C => %.1f°C). Boiler Temperature: %.1f°C, Target Setpoint: %.1f°C",
            old_value, self._current, boiler_temperature, target_setpoint
        )

        return self._current
