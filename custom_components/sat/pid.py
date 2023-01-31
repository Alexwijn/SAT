import time
from collections import deque
from typing import Optional

from homeassistant.core import State

from .const import *


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, kp: float, ki: float, kd: float,
                 max_history: int = 10,
                 deadband: float = 0.1,
                 automatic_gains: bool = False,
                 integral_time_limit: float = 300,
                 sample_time_limit: Optional[float] = 10,
                 heating_system: str = HEATING_SYSTEM_RADIATOR_LOW_TEMPERATURES):
        """
        Initialize the PID controller.

        :param kp: The proportional gain of the PID controller.
        :param ki: The integral gain of the PID controller.
        :param kd: The derivative gain of the PID controller.
        :param max_history: The maximum number of errors and time values to store to calculate the derivative term.
        :param deadband: The deadband of the PID controller. The range of error values where the controller will not make adjustments.
        :param integral_time_limit: The minimum time interval between integral updates to the PID controller, in seconds.
        :param sample_time_limit: The minimum time interval between updates to the PID controller, in seconds.
        :param heating_system: The heating system type that we are controlling.
        """
        self._kp = kp
        self._ki = ki
        self._kd = kd
        self._deadband = deadband
        self._history_size = max_history
        self._heating_system = heating_system
        self._automatic_gains = automatic_gains
        self._sample_time_limit = max(sample_time_limit, 1)
        self._integral_time_limit = max(integral_time_limit, 1)
        self.reset()

    def reset(self) -> None:
        """Reset the PID controller."""
        self._last_error = 0
        self._time_elapsed = 0
        self._raw_derivative = 0
        self._last_updated = time.time()
        self._last_heating_curve_value = 0

        # Reset the integral
        self._integral = 0
        self._integral_enabled = False

        # Reset all lists
        self._times = deque(maxlen=self._history_size)
        self._errors = deque(maxlen=self._history_size)

    def update(self, error: float, heating_curve_value: float) -> None:
        """Update the PID controller with the current error, inside temperature, outside temperature, and heating curve value.

        :param error: The max error between all the target temperatures and the current temperatures.
        :param heating_curve_value: The current heating curve value.
        """
        current_time = time.time()
        time_elapsed = current_time - self._last_updated

        if error == self._last_error:
            return

        if not self._integral_enabled and error <= 0:
            self._integral_enabled = True

        if self._sample_time_limit and time_elapsed < self._sample_time_limit:
            return

        self._last_error = error
        self._last_heating_curve_value = heating_curve_value
        self.update_integral(error, time_elapsed, heating_curve_value, True)

        self._last_updated = current_time
        self._time_elapsed = time_elapsed

        if abs(error) <= self._deadband:
            self._times.clear()
            self._errors.clear()
            self._raw_derivative = 0
            return

        self._errors.append(error)
        self._times.append(current_time)

        self._update_derivative()
        self._update_history_size()

    def update_reset(self, error: float, heating_curve_value: Optional[float]) -> None:
        """Update the PID controller with resetting.

        :param error: The error value for the PID controller to use in the update.
        :param heating_curve_value: The current value of the heating curve.
        """
        self._integral = 0
        self._time_elapsed = 0
        self._raw_derivative = 0

        self._last_error = error
        self._last_updated = time.time()
        self._last_interval_updated = time.time()
        self._last_heating_curve_value = heating_curve_value

        self._integral_enabled = error <= 0

    def update_integral(self, error: float, time_elapsed: float, heating_curve_value: float, force: bool = False):
        """
        Update the integral value in the PID controller.

        :param error: The error value for the current iteration.
        :param time_elapsed: The amount of time elapsed since the last update.
        :param heating_curve_value: The current value of the heating curve.
        :param force: Boolean flag indicating whether to force an update even if the integral time limit has not been reached.
        """
        # Make sure the integral term is enabled
        if not self._integral_enabled:
            return

        # Check if we are within the limit for updating the integral term
        # or if we are forcing an update
        if not force and time.time() - self._last_interval_updated < self._integral_time_limit:
            return

        limit = heating_curve_value / 10

        # Check if the integral gain `ki` is set
        if self.ki is None:
            return

        # Update the integral value
        self._integral += self.ki * error * time_elapsed

        # Clamp the integral value within the limit
        self._integral = min(self._integral, int(+limit))
        self._integral = max(self._integral, int(-limit))

        # Record the time of the latest update
        self._last_interval_updated = time.time()

    def _update_derivative(self, alpha1: float = 0.4, alpha2: float = 0.3):
        """
        Update the derivative term of the PID controller based on the latest error.

        The derivative term is calculated as the slope of the line connecting the
        first and last error values in the error history, and is then filtered twice
        using low-pass filters with parameters `alpha1` and `alpha2`.

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
        # If there are less than two errors in the history, we cannot calculate the derivative.
        if len(self._errors) < 2:
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

    def _update_history_size(self, alpha: float = 0.8):
        """
        Update the size of the history of errors and times.

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
        history_size = int(updates_per_second * 300)
        history_size = max(10, history_size)
        history_size = min(history_size, 100)

        # Calculate a weighted average of the rate of updates and the previous history size
        self._history_size = alpha * history_size + (1 - alpha) * self._history_size

    def restore(self, state: State) -> None:
        """Restore the PID controller from a saved state.

        state: The saved state of the PID controller to restore from.
        """
        if last_error := state.attributes.get("error"):
            self._last_error = last_error

        if last_integral := state.attributes.get("integral"):
            self._integral = last_integral

        if last_heating_curve := state.attributes.get("heating_curve"):
            self._last_heating_curve_value = last_heating_curve

    @property
    def last_error(self) -> float:
        """Return the last error value used by the PID controller."""
        return self._last_error

    @property
    def last_updated(self) -> float:
        """Return the timestamp of the last update to the PID controller."""
        return self._last_updated

    @property
    def kp(self) -> float | None:
        """Return the value of kp based on the current configuration."""
        if self._automatic_gains:
            return self._last_heating_curve_value

        return self._kp

    @property
    def ki(self) -> float | None:
        """Return the value of ki based on the current configuration."""
        if self._automatic_gains:
            if self._last_heating_curve_value is None:
                return 0

            return round(self._last_heating_curve_value / 73900, 6)

        return self._ki

    @property
    def kd(self) -> float | None:
        """Return the value of kd based on the current configuration."""
        if self._automatic_gains:
            if self._last_heating_curve_value is None:
                return 0

            if self._heating_system == HEATING_SYSTEM_RADIATOR_LOW_TEMPERATURES:
                return self._last_heating_curve_value * 739

            return self._last_heating_curve_value * 1000

        return self._kd

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
    def output(self) -> float:
        """Return the control output value."""
        return self.proportional + self.integral + self.derivative

    @property
    def integral_enabled(self) -> bool:
        """Return whether the updates of the integral are enabled."""
        return self._integral_enabled

    @property
    def num_errors(self) -> int:
        """Return the number of errors collected."""
        return len(self._errors)

    @property
    def history_size(self) -> int:
        """Return the number of values that we store."""
        return self._history_size
