import time
from collections import deque
from typing import Optional

from homeassistant.core import State


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, kp: float, ki: float, kd: float,
                 max_history: int = 10,
                 deadband: float = 0.1,
                 automatic_gains: bool = False,
                 integral_time_limit: float = 300,
                 sample_time_limit: Optional[float] = 10):
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
        self._automatic_gains = automatic_gains
        self._sample_time_limit = max(sample_time_limit, 1)
        self._integral_time_limit = max(integral_time_limit, 1)
        self.reset()

    def reset(self) -> None:
        """Reset the PID controller."""
        self._last_error = 0
        self._time_elapsed = 0
        self._last_updated = time.time()
        self._last_heating_curve_value = 0

        # Reset the integral
        self._integral = 0
        self._integral_enabled = False

        # Reset all lists
        self._outputs = []
        self._times = deque(maxlen=self._max_history)
        self._errors = deque(maxlen=self._max_history)

        # Reset autotune flag
        self._autotune_enabled = False
        self._optimal_kp = None
        self._optimal_ki = None
        self._optimal_kd = None

    def enable_autotune(self, enabled: bool) -> None:
        """Enable or disable the autotune feature.

        When enabled, the autotune feature will store the outputs of the PID
        controller for later use in calculating the optimal values for the PID gains.

        Parameters:
        enabled: A boolean indicating whether to enable (True) or disable (False) the autotune feature.
        """
        # Reset outputs when disabling or enabling autotune
        self._outputs = []
        self._autotune_enabled = enabled

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

        if abs(error) <= self._deadband:
            self._times.clear()
            self._errors.clear()
            return

        self._last_updated = current_time
        self._time_elapsed = time_elapsed

        self._errors.append(error)
        self._times.append(current_time)
        self._update_derivative(error)

        if self._autotune_enabled:
            self._outputs.append((self.output, heating_curve_value, time_elapsed))

    def update_reset(self, error: float, heating_curve_value: float) -> None:
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

    def _update_derivative(self, alpha: float = 0.5):
        if len(self._errors) < 2:
            return

        num_of_errors = len(self._errors)
        time_elapsed = self._times[num_of_errors - 1] - self._times[0]
        derivative_error = self._errors[num_of_errors - 1] - self._errors[0]

        derivative = (derivative_error / time_elapsed)
        self._raw_derivative = alpha * derivative + (1 - alpha) * self._raw_derivative

    def restore(self, state: State) -> None:
        """Restore the PID controller from a saved state.

        Parameters:
        state: The saved state of the PID controller to restore from.
        """
        if last_error := state.attributes.get("error"):
            self._last_error = last_error

            if self._last_error > 0:
                self.enable_autotune(True)

        if last_integral := state.attributes.get("integral"):
            self._integral = last_integral

        if last_heating_curve := state.attributes.get("heating_curve"):
            self._last_heating_curve_value = last_heating_curve

    def autotune(self, comfort_temp: float) -> None:
        """Calculate and set the optimal PID gains and heating curve based on previous outputs and temperatures."""
        self.determine_optimal_pid_gains()

        # Reset autotune flag
        self.enable_autotune(False)

    def determine_optimal_pid_gains(self) -> None:
        """Calculate the optimal PID gains based on the previous outputs."""
        # Get the list of outputs and the length of the list
        outputs = self._outputs
        num_outputs = len(outputs)

        # Check if there are enough outputs
        if num_outputs < 2:
            raise ValueError("Not enough outputs for autotune")

        # Initialize variables
        sum_output = 0
        sum_time_elapsed = 0
        sum_output_squared = 0
        sum_output_time_elapsed = 0
        sum_integral = 0

        # Iterate through the outputs
        previous_output = 0
        previous_time_elapsed = 0

        for output, heating_curve_value, time_elapsed in outputs:
            setpoint = heating_curve_value - output

            sum_output += setpoint
            sum_time_elapsed += time_elapsed
            sum_output_squared += setpoint * setpoint
            sum_output_time_elapsed += setpoint * time_elapsed
            sum_integral += (setpoint + previous_output) * (time_elapsed - previous_time_elapsed) / 2

            previous_output = setpoint
            previous_time_elapsed = time_elapsed

        # Calculate the slope and y-intercept
        slope = (num_outputs * sum_output_time_elapsed - sum_output * sum_time_elapsed) / (num_outputs * sum_output_squared - sum_output * sum_output)
        y_intercept = (sum_time_elapsed - slope * sum_output) / num_outputs

        # Calculate the optimal gains
        kp = (1 - y_intercept) / slope
        ki = sum_integral / sum_output
        kd = slope / (1 - y_intercept)

        # Store the latest autotune values
        self._optimal_kp = round(kp, 2)
        self._optimal_ki = round(ki, 2)
        self._optimal_kd = round(kd, 2)

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
        if self._automatic_gains:
            return self._last_heating_curve_value

        return self._kp

    @property
    def ki(self) -> float | None:
        if self._automatic_gains:
            if self._last_heating_curve_value is None:
                return None

            return round(self._last_heating_curve_value / 73900, 6)

        return self._ki

    @property
    def kd(self) -> float | None:
        if self._automatic_gains:
            if self._last_heating_curve_value is None:
                return None

            return self._last_heating_curve_value * 739

        return self._kd

    @property
    def proportional(self) -> float:
        """Return the proportional value."""
        if self.kp is None:
            return 0

        return round(self.kp * self._last_error, 3)

    @property
    def integral(self) -> float:
        """Return the integral value."""
        return round(self._integral, 3)

    @property
    def derivative(self) -> float:
        """Return the derivative value."""
        if self.kd is None:
            return 0

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
    def autotune_enabled(self) -> bool:
        """Return a boolean indicating whether autotune is enabled."""
        return self._autotune_enabled

    @property
    def num_errors(self) -> int:
        """Return the number of errors collected."""
        return len(self._errors)

    @property
    def num_outputs(self) -> int:
        """Return the number of updates collected."""
        return len(self._outputs)

    @property
    def optimal_kp(self) -> float:
        """Return the optimal proportional gain."""
        return self._optimal_kp

    @property
    def optimal_ki(self) -> float:
        """Return the optimal integral gain."""
        return self._optimal_ki

    @property
    def optimal_kd(self) -> float:
        """Return the optimal derivative gain."""
        return self._optimal_kd
