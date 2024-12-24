import logging
from time import time

from .const import BOILER_TEMPERATURE_OFFSET, MINIMUM_SETPOINT

_LOGGER = logging.getLogger(__name__)
ADJUSTMENT_FACTOR = 0.5


class MinimumSetpoint:
    def __init__(self, adjustment_delay: int):
        """Initialize the MinimumSetpoint class."""
        self._warmed_up_time = None
        self._adjustment_delay = adjustment_delay

        self._current = None

    def warming_up(self, flame_active: bool, boiler_temperature: float) -> None:
        """Set the minimum setpoint to trigger the boiler flame during warm-up."""
        if flame_active:
            _LOGGER.debug("Flame is already active. Skipping warm-up adjustment.")
            return

        self._current = boiler_temperature + 10

        _LOGGER.info(
            "Warm-up adjustment applied: %.1f°C (Boiler Temperature: %.1f°C)",
            self._current, boiler_temperature
        )

    def calculate(self, requested_setpoint: float, boiler_temperature: float) -> None:
        """Adjust the minimum setpoint based on the requested setpoint and boiler temperature."""
        if self._current is None or self._warmed_up_time is None:
            self._initialize_setpoint(boiler_temperature)

        if not self._should_apply_adjustment():
            _LOGGER.debug("Adjustment skipped. Waiting for adjustment delay (%d seconds).", self._adjustment_delay)
            return

        old_value = self._current
        target_setpoint = boiler_temperature - BOILER_TEMPERATURE_OFFSET

        if self._current < target_setpoint:
            self._current = min(self._current + ADJUSTMENT_FACTOR, target_setpoint)
        else:
            self._current = max(self._current - ADJUSTMENT_FACTOR, target_setpoint)

        _LOGGER.info(
            "Minimum setpoint changed (%.1f°C => %.1f°C). Boiler Temperature: %.1f°C, Requested Setpoint: %.1f°C, Target: %.1f°C",
            old_value, boiler_temperature, requested_setpoint, self._current, target_setpoint
        )

    def _should_apply_adjustment(self) -> bool:
        """Check if the adjustment factor can be applied based on delay."""
        if self._warmed_up_time is None:
            return False

        return (time() - self._warmed_up_time) > self._adjustment_delay

    def _initialize_setpoint(self, boiler_temperature: float) -> None:
        """Initialize the current minimum setpoint if it is not already set."""
        self._warmed_up_time = time()
        self._current = boiler_temperature

        _LOGGER.info(
            "Initial minimum setpoint set to boiler temperature: %.1f°C. Time: %.1f",
            boiler_temperature, self._warmed_up_time
        )

    def current(self) -> float:
        """Return the current minimum setpoint."""
        if self._current is not None:
            return round(self._current, 1)

        return MINIMUM_SETPOINT
