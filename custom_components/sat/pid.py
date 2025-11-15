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

    def __init__(
            self,
            heating_system: str,
            automatic_gain_value: float,
            heating_curve_coefficient: float,
            derivative_time_weight: float,
            kp: float,
            ki: float,
            kd: float,
            deadband: float = DEADBAND,
            automatic_gains: bool = False,
            integral_time_limit: float = 300,
            sample_time_limit: Optional[float] = 10,
            version: int = 3,
    ) -> None:
        """
        Initialize the PID controller.

        :param heating_system: The type of heating system, either "underfloor" or "radiator"
        :param automatic_gain_value: The value to fine-tune the aggression value.
        :param heating_curve_coefficient: The heating curve coefficient.
        :param derivative_time_weight: The weight to fine-tune the derivative.
        :param kp: The proportional gain of the PID controller.
        :param ki: The integral gain of the PID controller.
        :param kd: The derivative gain of the PID controller.
        :param deadband: The deadband of the PID controller.
        :param integral_time_limit: Minimum time between integral updates in seconds.
        :param sample_time_limit: Minimum time between PID updates, in seconds.
        :param version: The version of the automatic gain calculation.
        """
        self._kp: float = kp
        self._ki: float = ki
        self._kd: float = kd
        self._version: int = version
        self._deadband: float = deadband
        self._heating_system: str = heating_system
        self._automatic_gains: bool = automatic_gains
        self._automatic_gains_value: float = automatic_gain_value
        self._derivative_time_weight: float = derivative_time_weight
        self._heating_curve_coefficient: float = heating_curve_coefficient

        self._last_interval_updated: float = monotonic()
        self._integral_time_limit: float = max(integral_time_limit, 1)
        self._sample_time_limit: Optional[float] = (max(sample_time_limit, 1) if sample_time_limit is not None else None)

        self.reset()

    def reset(self) -> None:
        """Reset the PID controller."""
        now = monotonic()

        self._time_elapsed: float = 0.0
        self._last_updated: float = now
        self._last_derivative_time: float = now
        self._last_interval_updated: float = now

        self._last_error: float = 0.0
        self._previous_error: float = 0.0
        self._last_heating_curve_value: float = 0.0
        self._last_boiler_temperature: float | None = None

        # Reset integral and derivative
        self._integral: float = 0.0
        self._raw_derivative: float = 0.0

    def update(self, error: Error, heating_curve_value: float, boiler_temperature: float) -> None:
        """
        Update the PID controller with the current error and heating curve value.

        :param error: The max error between all the target temperatures and the current temperatures.
        :param heating_curve_value: The current heating curve value.
        :param boiler_temperature: The current boiler temperature.
        """
        time_elapsed = seconds_since(self._last_updated)

        # If nothing changed, skip
        if error.value == self._last_error:
            return

        # Enforce minimum sample time if configured
        if self._sample_time_limit is not None and time_elapsed < self._sample_time_limit:
            return

        # Update integral and derivative based on the previously stored error
        self.update_integral(error, heating_curve_value, True)
        self.update_derivative(error)

        self._time_elapsed = time_elapsed
        self._last_updated = monotonic()

        self._previous_error = self._last_error
        self._last_error = error.value

        self._last_boiler_temperature = boiler_temperature
        self._last_heating_curve_value = heating_curve_value

    def update_reset(self, error: Error, heating_curve_value: Optional[float]) -> None:
        """
        Update the PID controller with resetting.

        :param error: The error value for the PID controller to use in the update.
        :param heating_curve_value: The current value of the heating curve.
        """
        now = monotonic()

        self._last_updated = now
        self._last_derivative_time = now
        self._last_interval_updated = now

        self._integral = 0.0
        self._time_elapsed = 0.0
        self._raw_derivative = 0.0

        self._last_error = error.value
        self._previous_error = error.value
        self._last_heating_curve_value = heating_curve_value if heating_curve_value is not None else 0.0

    def update_integral(self, error: Error, heating_curve_value: float, force: bool = False) -> None:
        """
        Update the integral value in the PID controller.

        :param error: The error value for the current iteration.
        :param heating_curve_value: The current value of the heating curve.
        :param force: Force an update even if the integral time limit has not been reached.
        """
        # Reset the time if we just entered deadband
        if abs(self.last_error) > self._deadband >= abs(error.value):
            self._last_interval_updated = monotonic()

        # Ensure the integral term is enabled
        if not self.integral_enabled:
            self._integral = 0.0
            return

        # Check the time limit for updating the integral term
        if not force and monotonic() - self._last_interval_updated < self._integral_time_limit:
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

    def _get_aggression_value(self) -> float:
        if self._version == 1:
            return 73 if self._heating_system == HEATING_SYSTEM_UNDERFLOOR else 99

        if self._version == 2:
            return 73 if self._heating_system == HEATING_SYSTEM_UNDERFLOOR else 81.5

        if self._version == 3:
            return 8400

        raise Exception("Invalid version")

    @property
    def last_error(self) -> float:
        """Return the last error value used by the PID controller."""
        return self._last_error

    @property
    def previous_error(self) -> float:
        """Return the previous error value used by the PID controller."""
        return self._previous_error

    @property
    def last_updated(self) -> float:
        """Return the timestamp of the last update to the PID controller."""
        return self._last_updated

    @property
    def kp(self) -> float | None:
        """Return the value of kp based on the current configuration."""
        if self._automatic_gains:
            if self._version == 3:
                automatic_gain_value = 4 if self._heating_system == HEATING_SYSTEM_UNDERFLOOR else 3
                return round((self._heating_curve_coefficient * self._last_heating_curve_value) / automatic_gain_value, 6)

            automatic_gain_value = 0.243 if self._heating_system == HEATING_SYSTEM_UNDERFLOOR else 0.33
            return round(self._automatic_gains_value * automatic_gain_value * self._last_heating_curve_value, 6)

        return float(self._kp)

    @property
    def ki(self) -> float | None:
        """Return the value of ki based on the current configuration."""
        if self._automatic_gains:
            if self._last_heating_curve_value is None:
                return 0.0

            if self._version == 1:
                return round(self._last_heating_curve_value / 73900, 6)

            if self._version == 2:
                return round(self._automatic_gains_value * (self._last_heating_curve_value / 7200), 6)

            if self._version == 3:
                return round(self.kp / self._get_aggression_value(), 6)

            raise Exception("Invalid version")

        return float(self._ki)

    @property
    def kd(self) -> float | None:
        """Return the value of kd based on the current configuration."""
        if self._automatic_gains:
            if self._last_heating_curve_value is None:
                return 0.0

            if self._version == 3:
                return round(0.07 * self._get_aggression_value() * self.kp, 6)

            return round(self._automatic_gains_value * self._get_aggression_value() * self._derivative_time_weight * self._last_heating_curve_value, 6)

        return float(self._kd)

    @property
    def proportional(self) -> float:
        """Return the proportional value."""
        return round(self.kp * self._last_error, 3) if self.kp is not None else 0.0

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
        return self.proportional + self.integral + self.derivative

    @property
    def integral_enabled(self) -> bool:
        """Return whether the updates of the integral are enabled."""
        return abs(self._last_error) <= self._deadband

    @property
    def derivative_enabled(self) -> bool:
        """Return whether the updates of the derivative are enabled."""
        return abs(self._last_error) > self._deadband
