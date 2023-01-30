import time
from bisect import bisect_left
from collections import deque
from itertools import islice
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

        Parameters:
        kp: The proportional gain of the PID controller.
        ki: The integral gain of the PID controller.
        kd: The derivative gain of the PID controller.
        max_history: The maximum number of errors and time values to store to calculate the derivative term.
        integral_time_limit: The minimum time interval between integral updates to the PID controller, in seconds.
        sample_time_limit: The minimum time interval between updates to the PID controller, in seconds.
        deadband: The deadband of the PID controller. The range of error values where the controller will not make adjustments.
        """
        self._kp = kp
        self._ki = ki
        self._kd = kd
        self._deadband = deadband
        self._max_history = max_history
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
        self._times = deque(maxlen=self._max_history)
        self._errors = deque(maxlen=self._max_history)

    def update(self, error: float, heating_curve_value: float) -> None:
        """Update the PID controller with the current error, inside temperature, outside temperature, and heating curve value.
        Parameters:
        error: The max error between all the target temperatures and the current temperatures.
        heating_curve_value: The current heating curve value.
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
        self._update_derivative(error)

    def update_reset(self, error: float, heating_curve_value: Optional[float]) -> None:
        """Update the PID controller with resetting.

        Parameters:
        error: The error value for the PID controller to use in the update.
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
        # Make sure it is enabled
        if not self._integral_enabled:
            return

        # Check if we are outside the limit, or we are forcing it to update
        if not force and time.time() - self._last_interval_updated < self._integral_time_limit:
            return

        limit = heating_curve_value / 10

        if self.ki is None:
            return

        self._integral += self.ki * error * time_elapsed
        self._integral = min(self._integral, int(+limit))
        self._integral = max(self._integral, int(-limit))

        self._last_interval_updated = time.time()

    def _update_derivative(self, alpha: float = 0.5, window_size: int = 300):
        if len(self._errors) < 2:
            return

        # Find the indices of the errors and times within the window
        window_start = bisect_left(self._times, self._times[-1] - window_size)
        errors_in_window = list(islice(self._errors, window_start, None))
        times_in_window = list(islice(self._times, window_start, None))

        # Calculate the derivative using the errors and times in the window
        derivative_error = errors_in_window[0] - errors_in_window[-1]
        time_elapsed = times_in_window[0] - times_in_window[-1]
        derivative = derivative_error / time_elapsed

        # Apply the low-pass filter
        self._raw_derivative = alpha * derivative + (1 - alpha) * self._raw_derivative

    def restore(self, state: State) -> None:
        """Restore the PID controller from a saved state.

        Parameters:
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
