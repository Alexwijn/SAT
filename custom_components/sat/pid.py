import logging
from collections import deque
from time import monotonic
from typing import Optional

from homeassistant.core import State

from .const import *
from .helpers import seconds_since

_LOGGER = logging.getLogger(__name__)

MAX_BOILER_TEMPERATURE_AGE = 300


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self,
                 heating_system: str,
                 automatic_gain_value: float,
                 heating_curve_coefficient: float,
                 derivative_time_weight: float,
                 kp: float, ki: float, kd: float,
                 max_history: int = 2,
                 deadband: float = DEADBAND,
                 automatic_gains: bool = False,
                 integral_time_limit: float = 300,
                 sample_time_limit: Optional[float] = 10,
                 version: int = 3):
        """
        Initialize the PID controller.

        :param heating_system: The type of heating system, either "underfloor" or "radiator"
        :param automatic_gain_value: The value to fine-tune the aggression value.
        :param derivative_time_weight: The weight to fine-tune the derivative.
        :param kp: The proportional gain of the PID controller.
        :param ki: The integral gain of the PID controller.
        :param kd: The derivative gain of the PID controller.
        :param max_history: The maximum number of errors and time values to store to calculate the derivative term.
        :param deadband: The deadband of the PID controller. The range of error values where the controller will not make adjustments.
        :param integral_time_limit: The minimum time interval between integral updates to the PID controller, in seconds.
        :param sample_time_limit: The minimum time interval between updates to the PID controller, in seconds.
        :param version: The version of math calculation for the controller.
        """
        self._kp: float = kp
        self._ki: float = ki
        self._kd: float = kd
        self._version: int = version
        self._deadband: float = deadband
        self._history_size: int = max_history
        self._heating_system: str = heating_system
        self._automatic_gains: bool = automatic_gains
        self._automatic_gains_value: float = automatic_gain_value
        self._derivative_time_weight: float = derivative_time_weight
        self._heating_curve_coefficient: float = heating_curve_coefficient

        self._last_interval_updated: float = monotonic()
        self._sample_time_limit: float = max(sample_time_limit, 1)
        self._integral_time_limit: float = max(integral_time_limit, 1)
        self.reset()

    def reset(self) -> None:
        """Reset the PID controller."""
        self._last_error: float = 0.0
        self._time_elapsed: float = 0
        self._last_updated: float = monotonic()
        self._last_heating_curve_value: float = 0
        self._last_boiler_temperature: float | None = None

        # Reset the integral and derivative
        self._integral: float = 0.0
        self._raw_derivative: float = 0.0

        # Reset all lists
        self._times: deque = deque(maxlen=self._history_size)
        self._errors: deque = deque(maxlen=self._history_size)

    def update(self, error: float, heating_curve_value: float, boiler_temperature: float) -> None:
        """Update the PID controller with the current error, inside temperature, outside temperature, and heating curve value.

        :param error: The max error between all the target temperatures and the current temperatures.
        :param heating_curve_value: The current heating curve value.
        :param boiler_temperature: The current boiler temperature.
        """
        time_elapsed = seconds_since(self._last_updated)

        if error == self._last_error:
            return

        if self._sample_time_limit and time_elapsed < self._sample_time_limit:
            return

        self.update_integral(error, heating_curve_value, True)
        self.update_derivative(error)
        self.update_history_size()

        self._last_updated = monotonic()
        self._time_elapsed = time_elapsed

        self._last_error = error
        self._last_boiler_temperature = boiler_temperature
        self._last_heating_curve_value = heating_curve_value

    def update_reset(self, error: float, heating_curve_value: Optional[float]) -> None:
        """Update the PID controller with resetting.

        :param error: The error value for the PID controller to use in the update.
        :param heating_curve_value: The current value of the heating curve.
        """
        self._integral = 0
        self._time_elapsed = 0
        self._raw_derivative = 0

        self._last_error = error
        self._last_updated = monotonic()
        self._last_interval_updated = monotonic()
        self._last_heating_curve_value = heating_curve_value

        self._errors = deque([error], maxlen=int(self._history_size))
        self._times = deque([monotonic()], maxlen=int(self._history_size))

    def update_integral(self, error: float, heating_curve_value: float, force: bool = False):
        """
        Update the integral value in the PID controller.

        :param error: The error value for the current iteration.
        :param heating_curve_value: The current value of the heating curve.
        :param force: Boolean flag indicating whether to force an update even if the integral time limit has not been reached.
        """
        # Make sure we reset the time if we just entered deadband
        if abs(self.last_error) > self._deadband >= abs(error):
            self._last_interval_updated = monotonic()

        # Make sure the integral term is enabled
        if not self.integral_enabled:
            self._integral = 0
            return

        # Check if we are within the limit for updating the integral term
        # or if we are forcing an update
        if not force and monotonic() - self._last_interval_updated < self._integral_time_limit:
            return

        # Check if the integral gain `ki` is set
        if self.ki is None:
            return

        # Update the integral value
        self._integral += self.ki * error * seconds_since(self._last_interval_updated)

        # Clamp the integral value within the limit
        self._integral = min(self._integral, float(+heating_curve_value))
        self._integral = max(self._integral, float(-heating_curve_value))

        # Record the time of the latest update
        self._last_interval_updated = monotonic()

    def update_derivative(self, error: float, alpha1: float = 0.8, alpha2: float = 0.6):
        """
        Update the derivative term of the PID controller based on the latest error.

        The derivative term is calculated as the slope of the line connecting the
        first and last error values in the error history, and is then filtered twice
        using low-pass filters with parameters `alpha1` and `alpha2`.

        :param error:  The error value for the current iteration.
        :param alpha1: The first low-pass filter parameter. It determines the weight given to
                       the new derivative value relative to the previous value.
                       A value of 1.0 corresponds to no filtering, and a value of 0.0
                       corresponds to only using the previous value.
        :param alpha2: The second low-pass filter parameter. It determines the weight given to
                       the filtered derivative value from the first low-pass filter relative to
                       the previous filtered value.
                       A value of 1.0 corresponds to no filtering, and a value of 0.0
                       corresponds to only using the previous filtered value.
        """
        # Fill the history
        self._errors.append(error)
        self._times.append(monotonic())

        # If there are less than two errors in the history, we cannot calculate the derivative.
        if len(self._errors) < 2:
            return

        # If "derivative" is disabled, we freeze it
        if not self.derivative_enabled:
            return

        # Calculate the derivative using the errors and times in the error history.
        num_of_errors = len(self._errors)
        time_elapsed = self._times[num_of_errors - 1] - self._times[0]
        derivative_error = self._errors[num_of_errors - 1] - self._errors[0]
        derivative = derivative_error / time_elapsed

        # Apply the first low-pass filter to the derivative.
        filtered_derivative = alpha1 * derivative + (1 - alpha1) * self._raw_derivative

        # Apply the second low-pass filter to the filtered derivative.
        self._raw_derivative = alpha2 * filtered_derivative + (1 - alpha2) * self._raw_derivative

    def update_history_size(self, alpha: float = 0.8):
        """
        Update the history of errors and times.

        The size of the history is updated based on the frequency of updates to the sensor value.
        If the frequency of updates is high, the history size is increased, and if the frequency of updates is low,
        the history size is decreased. The `alpha` parameter determines the weight given to the current history size
        versus the newly calculated history size.

        :param alpha: A weighting factor that determines the influence of the current history size on the updated history size.
        """
        num_of_errors = len(self._errors)
        if num_of_errors < 2:
            return

        # Calculate the rate of updates received
        time_diff = self._times[-1] - self._times[0]
        if time_diff == 0:
            return

        # Determine the report rate
        updates_per_second = len(self._times) / time_diff

        # Limit the history size to a maximum of 100
        history_size = int(updates_per_second * 3600)
        history_size = max(2, history_size)
        history_size = min(history_size, 100)

        # Calculate an average weighted rate of updates and the previous history size
        self._history_size = max(alpha * history_size + (1 - alpha) * self._history_size, 1)

        # Update our lists with the new size
        self._errors = deque(self._errors, maxlen=int(self._history_size))
        self._times = deque(self._times, maxlen=int(self._history_size))

    def restore(self, state: State) -> None:
        """Restore the PID controller from a saved state."""
        if last_error := state.attributes.get("error"):
            self._last_error = last_error

        if last_integral := state.attributes.get("integral"):
            self._integral = last_integral

        if last_raw_derivative := state.attributes.get("derivative_raw"):
            self._raw_derivative = last_raw_derivative

        if last_heating_curve := state.attributes.get("heating_curve"):
            self._last_heating_curve_value = last_heating_curve

    def _get_aggression_value(self):
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
        if len(self._errors) < 2:
            return self._last_error

        return self._errors[-2]

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
                return 0

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
                return 0

            if self._version == 3:
                return round(0.07 * self._get_aggression_value() * self.kp, 6)

            return round(self._automatic_gains_value * self._get_aggression_value() * self._derivative_time_weight * self._last_heating_curve_value, 6)

        return float(self._kd)

    @property
    def proportional(self) -> float:
        """Return the proportional value."""
        return round(self.kp * self._last_error, 3)

    @property
    def integral(self) -> float:
        """Return the integral value."""
        return round(self._integral, 3)

    @property
    def derivative(self) -> float:
        """Return the derivative value."""
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

    @property
    def num_errors(self) -> int:
        """Return the number of errors collected."""
        return len(self._errors)

    @property
    def history_size(self) -> int:
        """Return the number of values that we store."""
        return int(self._history_size)
