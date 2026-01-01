import logging
from time import monotonic
from typing import Optional

from homeassistant.core import State

from .const import *
from .errors import Error
from .helpers import seconds_since

_LOGGER = logging.getLogger(__name__)

MAX_BOILER_TEMPERATURE_AGE = 300


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, heating_system: str, automatic_gain_value: float, heating_curve_coefficient: float, derivative_time_weight: float, kp: float, ki: float, kd: float, automatic_gains: bool = False) -> None:
        self._kp: float = kp
        self._ki: float = ki
        self._kd: float = kd
        self._heating_system: str = heating_system
        self._automatic_gains: bool = automatic_gains
        self._automatic_gains_value: float = automatic_gain_value
        self._derivative_time_weight: float = derivative_time_weight
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

    @property
    def integral_enabled(self) -> bool:
        """Return whether the updates of the integral are enabled."""
        return abs(self._last_error) <= DEADBAND if self._last_error is not None else False

    @property
    def derivative_enabled(self) -> bool:
        """Return whether the updates of the derivative are enabled."""
        return abs(self._last_error) > DEADBAND if self._last_error is not None else False

    @property
    def available(self):
        """Return whether the PID controller is available."""
        return self._last_error is not None and self._last_heating_curve_value is not None

    def reset(self) -> None:
        """Reset the PID controller."""
        now = monotonic()

        self._time_elapsed: float = 0.0
        self._last_updated: float = now
        self._last_derivative_time: float = now
        self._last_interval_updated: float = now

        self._last_error: Optional[float] = None
        self._previous_error: Optional[float] = None
        self._last_heating_curve_value: Optional[float] = None

        # Reset integral and derivative
        self._integral: float = 0.0
        self._raw_derivative: float = 0.0

    def update(self, error: Error, heating_curve_value: float) -> None:
        time_elapsed = seconds_since(self._last_updated)

        # Update integral and derivative based on the previously stored error
        self.update_integral(error, heating_curve_value)
        self.update_derivative(error)

        self._time_elapsed = time_elapsed
        self._last_updated = monotonic()

        self._previous_error = self._last_error
        self._last_error = error.value

        self._last_heating_curve_value = heating_curve_value

    def update_integral(self, error: Error, heating_curve_value: float) -> None:
        """
        Update the integral value in the PID controller.

        :param error: The error value for the current iteration.
        :param heating_curve_value: The current value of the heating curve.
        """
        # Reset the time if we just entered deadband
        if self._last_error is not None and abs(self._last_error) > DEADBAND >= abs(error.value):
            self._last_interval_updated = monotonic()

        # Ensure the integral term is enabled
        if not self.integral_enabled:
            self._integral = 0.0
            return

        # Check if integral gain is set
        if self.ki is None:
            return

        # Update the integral value
        delta_time = seconds_since(self._last_interval_updated)
        self._integral += self.ki * error.value * delta_time

        # Clamp the integral value within the limit
        self._integral = min(self._integral, float(+heating_curve_value))
        self._integral = max(self._integral, float(-heating_curve_value))

        # Record the time of the latest update
        self._last_interval_updated = monotonic()

    def update_derivative(self, error: Error, alpha1: float = 0.8, alpha2: float = 0.6) -> None:
        """
        Update the derivative term of the PID controller based on the latest error.

        The derivative term is calculated as the slope between the previous and current
        error values over time, then filtered twice using low-pass filters with
        parameters `alpha1` and `alpha2`.

        :param error:  The error value for the current iteration.
        :param alpha1: First low-pass filter parameter (0..1).
        :param alpha2: Second low-pass filter parameter (0..1).
        """
        if self._last_error is None:
            return

        # If the derivative is disabled, freeze it
        if not self.derivative_enabled:
            return

        now = monotonic()
        time_diff = now - self._last_derivative_time
        if time_diff <= 0:
            return

        # Basic derivative: slope between current and last error
        derivative = (error.value - self._last_error) / time_diff

        # First low-pass filter
        filtered_derivative = alpha1 * derivative + (1 - alpha1) * self._raw_derivative

        # Second low-pass filter
        self._raw_derivative = alpha2 * filtered_derivative + (1 - alpha2) * self._raw_derivative

        # Update derivative timestamp
        self._last_derivative_time = now

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
        self._last_derivative_time = now
        self._last_interval_updated = now
