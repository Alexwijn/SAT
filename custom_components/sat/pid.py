"""PID controller logic for supply-air temperature tuning."""

import logging
from time import monotonic
from typing import Optional

from homeassistant.core import State

from .const import *
from .errors import Error

_LOGGER = logging.getLogger(__name__)

DERIVATIVE_ALPHA1 = 0.8
DERIVATIVE_ALPHA2 = 0.6
ERROR_EPSILON = 0.01
DERIVATIVE_RAW_CAP = 5.0
DERIVATIVE_DECAY = 0.9
MAX_BOILER_TEMPERATURE_AGE = 300


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, heating_system: str, automatic_gain_value: float, heating_curve_coefficient: float, kp: float, ki: float, kd: float, automatic_gains: bool = False) -> None:
        self._kp: float = kp
        self._ki: float = ki
        self._kd: float = kd
        self._heating_system: str = heating_system
        self._automatic_gains: bool = automatic_gains
        self._automatic_gains_value: float = automatic_gain_value
        self._heating_curve_coefficient: float = heating_curve_coefficient

        self._last_interval_updated: float = monotonic()

        self.reset()

    @property
    def last_error(self) -> Optional[float]:
        """Return the last error value used by the PID controller."""
        return self._last_error

    @property
    def previous_error(self) -> Optional[float]:
        """Return the previous error value used by the PID controller."""
        return self._previous_error

    @property
    def last_updated(self) -> float:
        """Return the timestamp of the last update to the PID controller."""
        return self._last_updated

    @property
    def available(self):
        """Return whether the PID controller is available."""
        return self._last_error is not None and self._last_heating_curve_value is not None

    @property
    def kp(self) -> Optional[float]:
        """Return the value of kp based on the current configuration."""
        if not self._automatic_gains:
            return float(self._kp)

        if self._last_heating_curve_value is None:
            return 0.0

        automatic_gain_value = 4 if self._heating_system == HEATING_SYSTEM_UNDERFLOOR else 3
        return round((self._heating_curve_coefficient * self._last_heating_curve_value) / automatic_gain_value, 6)

    @property
    def ki(self) -> Optional[float]:
        """Return the value of ki based on the current configuration."""
        if not self._automatic_gains:
            return float(self._ki)

        return round(self.kp / 8400, 6)

    @property
    def kd(self) -> Optional[float]:
        """Return the value of kd based on the current configuration."""
        if not self._automatic_gains:
            return float(self._kd)

        return round(0.07 * 8400 * self.kp, 6)

    @property
    def proportional(self) -> float:
        """Return the proportional value."""
        return round(self.kp * self._last_error, 3) if self.kp is not None and self._last_error is not None else 0.0

    @property
    def integral(self) -> float:
        """Return the integral value."""
        return round(self._integral, 3)

    @property
    def derivative(self) -> float:
        """Return the derivative value."""
        if self.kd is None:
            return 0.0

        return round(self.kd * self._raw_derivative, 3)

    @property
    def raw_derivative(self) -> float:
        """Return the raw derivative value."""
        return round(self._raw_derivative, 3)

    @property
    def output(self) -> float:
        """Return the control output value."""
        if self._last_heating_curve_value is None:
            return 0.0

        return round(self._last_heating_curve_value + self.proportional + self.integral + self.derivative, 1)

    def reset(self) -> None:
        """Reset the PID controller to a clean state."""
        now = monotonic()

        self._time_elapsed: float = 0.0
        self._last_updated: float = now
        self._last_interval_updated: float = now

        self._last_error: Optional[float] = None
        self._previous_error: Optional[float] = None
        self._last_heating_curve_value: Optional[float] = None
        self._last_error_change_time: Optional[float] = None

        # Reset integral and derivative accumulators.
        self._integral: float = 0.0
        self._raw_derivative: float = 0.0

    def restore(self, state: State) -> None:
        """Restore the PID controller from a saved state."""
        if (last_error := state.attributes.get("error")) is not None:
            self._last_error = float(last_error)
            self._previous_error = float(last_error)

        if (last_integral := state.attributes.get("integral")) is not None:
            self._integral = float(last_integral)

        if (last_raw_derivative := state.attributes.get("derivative_raw")) is not None:
            self._raw_derivative = float(last_raw_derivative)

        if (last_heating_curve := state.attributes.get("heating_curve")) is not None:
            self._last_heating_curve_value = float(last_heating_curve)

        # After restore, reset timing anchors "now"
        now = monotonic()
        self._last_updated = now
        self._last_interval_updated = now
        self._last_error_change_time = now if self._last_error is not None else None

    def update(self, error: Error, heating_curve_value: float) -> None:
        """Update PID state with the latest error and heating curve value."""
        now = monotonic()
        time_elapsed = now - self._last_updated
        error_changed = self._last_error is None or abs(error.value - self._last_error) >= ERROR_EPSILON

        # Update integral and derivative based on the previously stored error.
        self._update_integral(error, now, heating_curve_value)
        self._update_derivative(error, now, error_changed)

        self._last_updated = now
        self._time_elapsed = time_elapsed
        self._last_heating_curve_value = heating_curve_value

        if error_changed:
            self._previous_error = self._last_error
            self._last_error_change_time = now
            self._last_error = error.value

    def update_integral(self, error: Error, heating_curve_value: float) -> None:
        """Update only the integral term using the current time."""
        now = monotonic()
        self._update_integral(error, now, heating_curve_value)

    def _update_integral(self, error: Error, now: float, heating_curve_value: float) -> None:
        """Update the integral value in the PID controller."""

        # Reset the time base if we just entered the deadband.
        if self._last_error is not None and abs(self._last_error) > DEADBAND >= abs(error.value):
            self._last_interval_updated = now

        # Ensure the integral term is enabled for the current error.
        if abs(error.value) > DEADBAND:
            self._integral = 0.0
            return

        # Check if integral gain is set.
        if self.ki is None:
            return

        # Update the integral value.
        delta_time = now - self._last_interval_updated
        self._integral += self.ki * error.value * delta_time

        # Clamp integral to avoid pushing beyond the curve bounds.
        self._integral = min(self._integral, float(+heating_curve_value))
        self._integral = max(self._integral, float(-heating_curve_value))

        # Record the time of the latest update.
        self._last_interval_updated = now

    def _update_derivative(self, error: Error, now: float, error_changed: bool) -> None:
        """Update the derivative term of the PID controller based on the latest error."""
        if self._last_error is None:
            return

        # If the derivative is disabled for the current error, freeze it.
        if abs(error.value) <= DEADBAND:
            self._raw_derivative *= DERIVATIVE_DECAY
            return

        if not error_changed:
            self._raw_derivative *= DERIVATIVE_DECAY
            return

        if self._last_error_change_time is None:
            return

        time_diff = now - self._last_error_change_time
        if time_diff <= 0:
            return

        # Basic derivative: slope between current and last error.
        derivative = (error.value - self._last_error) / time_diff

        # First low-pass filter.
        filtered_derivative = DERIVATIVE_ALPHA1 * derivative + (1 - DERIVATIVE_ALPHA1) * self._raw_derivative

        # Second low-pass filter.
        self._raw_derivative = DERIVATIVE_ALPHA2 * filtered_derivative + (1 - DERIVATIVE_ALPHA2) * self._raw_derivative
        self._raw_derivative = max(-DERIVATIVE_RAW_CAP, min(self._raw_derivative, DERIVATIVE_RAW_CAP))
