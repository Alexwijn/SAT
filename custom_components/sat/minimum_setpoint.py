import logging
from time import time

from .const import BOILER_TEMPERATURE_OFFSET

_LOGGER = logging.getLogger(__name__)


class MinimumSetpoint:
    def __init__(self, configured: float, smoothing_factor: float, adjustment_delay: int = 30):
        """Initialize the MinimumSetpoint class."""
        self._last_adjustment_time = None
        self._smoothing_factor = smoothing_factor
        self._adjustment_delay = adjustment_delay

        self._current = None
        self._configured = configured

    @staticmethod
    def _calculate_threshold(boiler_temperature: float, target_setpoint: float) -> float:
        """Calculate the threshold to determine adjustment method."""
        return max(1.0, 0.1 * abs(boiler_temperature - target_setpoint))

    def warming_up(self, flame_active: bool, boiler_temperature: float) -> None:
        """Set the minimum setpoint to trigger the boiler flame during warm-up."""
        if flame_active:
            _LOGGER.debug("Flame is already active. Skipping warm-up adjustment.")
            return

        self._last_adjustment_time = time()
        self._current = boiler_temperature + 10

        _LOGGER.debug(
            "Warm-up adjustment applied: %.1f°C (boiler temperature: %.1f°C)",
            self._current, boiler_temperature
        )

    def calculate(self, requested_setpoint: float, boiler_temperature: float) -> None:
        """Adjust the minimum setpoint based on the requested setpoint and boiler temperature."""
        if self._current is None:
            self._initialize_setpoint(boiler_temperature)

        old_value = self._current
        target_setpoint = min(requested_setpoint, boiler_temperature - BOILER_TEMPERATURE_OFFSET)

        # Determine adjustment method based on a threshold
        threshold = self._calculate_threshold(boiler_temperature, target_setpoint)
        adjustment_factor = 0.5 if self._should_apply_adjustment() else 0.0
        use_smoothing = abs(target_setpoint - self._current) > threshold

        if use_smoothing:
            self._current += self._smoothing_factor * (target_setpoint - self._current)
        else:
            if self._current < target_setpoint:
                self._current = min(self._current + adjustment_factor, target_setpoint)
            else:
                self._current = max(self._current - adjustment_factor, target_setpoint)

        _LOGGER.debug(
            "Minimum setpoint adjusted (%.1f°C => %.1f°C). Type: %s, Target: %.1f°C, Threshold: %.1f, Adjustment Factor: %.1f",
            old_value, self._current,
            "smoothing" if use_smoothing else "incremental",
            target_setpoint, threshold, adjustment_factor
        )

    def _should_apply_adjustment(self) -> bool:
        """Check if the adjustment factor can be applied based on delay."""
        if self._last_adjustment_time is None:
            return False

        return (time() - self._last_adjustment_time) > self._adjustment_delay

    def _initialize_setpoint(self, boiler_temperature: float) -> None:
        """Initialize the current minimum setpoint if it is not already set."""
        self._last_adjustment_time = time()
        self._current = boiler_temperature

        _LOGGER.info("Initial minimum setpoint set to boiler temperature: %.1f°C", boiler_temperature)

    def current(self) -> float:
        """Return the current minimum setpoint."""
        return round(self._current, 1) or self._configured
