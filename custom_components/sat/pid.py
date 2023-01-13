import time
from collections import deque
from typing import Optional

from homeassistant.core import State


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, kp: float, ki: float, kd: float,
                 deadband: (float, float) = (-0.1, 0.3),
                 output_average_size: int = 5,
                 derivative_average_size: int = 5,
                 sample_time_limit: Optional[float] = 0):
        """Initialize the PID controller.

        Parameters:
        kp: The proportional gain of the PID controller.
        ki: The integral gain of the PID controller.
        kd: The derivative gain of the PID controller.
        deadband: The deadband of the PID controller.
        output_average_size: The number of output values to average.
        derivative_average_size: The average size of the derivative errors
        sample_time_limit: The minimum time interval between updates to the PID controller, in seconds.
        """
        self._kp = kp
        self._ki = ki
        self._kd = kd
        self._deadband = deadband
        self._sample_time_limit = sample_time_limit
        self._output_average_size = output_average_size
        self._derivative_average_size = derivative_average_size
        self.reset()

    def reset(self) -> None:
        """Reset the PID controller."""
        self._last_error = 0
        self._time_elapsed = 0
        self._last_updated = time.time()

        # Reset the derivative
        self._derivative_errors = deque(maxlen=self._derivative_average_size)

        # Reset the integral
        self._integral = 0
        self._integral_enabled = True

        # Reset list of outputs
        self._autotune_outputs = []
        self._outputs = deque(maxlen=self._output_average_size)
        self._outputs.append(self.proportional + self.integral + self.derivative)

        # Reset autotune flag
        self._autotune_enabled = False
        self._optimal_kp = None
        self._optimal_ki = None
        self._optimal_kd = None

    def enable_integral(self, enabled: bool) -> None:
        """Enable or disable the updates of the integral.

        The integral component helps the PID controller to eliminate
        steady-state error by accumulating the error over time.

        Parameters:
        enabled: A boolean indicating whether to enable (True) or disable (False) the updates of the integral.
        """
        if self._integral_enabled != enabled:
            # Reset the integral if the enabled status changes
            self._integral = 0

        self._integral_enabled = enabled

    def enable_autotune(self, enabled: bool) -> None:
        """Enable or disable the autotune feature.

        When enabled, the autotune feature will store the outputs of the PID
        controller for later use in calculating the optimal values for the PID gains.

        Parameters:
        enabled: A boolean indicating whether to enable (True) or disable (False) the autotune feature.
        """
        # Reset outputs when disabling or enabling autotune
        self._autotune_outputs = []

        self._autotune_enabled = enabled

    def update(self, error: float, heating_curve_value: float) -> None:
        """Update the PID controller with the current error, inside temperature, outside temperature, and heating curve value.
        Parameters:
        error: The max error between all the target temperatures and the current temperatures.
        inside_temperature: The current inside temperature.
        outside_temperature: The current outside temperature.
        heating_curve_value: The current heating curve value.
        """
        current_time = time.time()
        time_elapsed = current_time - self._last_updated

        if error == self._last_error:
            return

        if self._sample_time_limit and time_elapsed < self._sample_time_limit:
            return

        if self._deadband[0] <= error <= self._deadband[1]:
            self._integral = 0
            self._derivative_errors.clear()
            return

        self._last_updated = current_time
        self._time_elapsed = time_elapsed

        if self._integral_enabled:
            self._integral += self._ki * error * time_elapsed

        self._last_error = error
        self._derivative_errors.append(self._last_error)
        self._outputs.append(self.proportional + self.integral + self.derivative)

        if self._autotune_enabled:
            # Collect the outputs in order to calculate an optimal pid gain
            self._autotune_outputs.append((self.output, heating_curve_value, time_elapsed))

    def update_reset(self, error: float) -> None:
        """Update the PID controller with resetting.

        Parameters:
        error: The error value for the PID controller to use in the update.
        """
        self._integral = 0
        self._time_elapsed = 0

        self._last_error = error
        self._last_updated = time.time()

    def restore(self, state: State) -> None:
        """Restore the PID controller from a saved state.

        Parameters:
        state: The saved state of the PID controller to restore from.
        """
        if last_error := state.attributes.get("error"):
            self._last_error = last_error

        if last__integral := state.attributes.get("integral"):
            self._integral = last__integral

    def autotune(self, comfort_temp: float) -> None:
        """Calculate and set the optimal PID gains and heating curve based on previous outputs and temperatures."""
        self.determine_optimal_pid_gains()

        # Reset autotune flag
        self.enable_autotune(False)

    def determine_optimal_pid_gains(self) -> None:
        """Calculate the optimal PID gains based on the previous outputs."""
        # Get the list of outputs and the length of the list
        outputs = self._autotune_outputs
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
    def proportional(self) -> float:
        """Return the proportional value."""
        return round(self._kp * self._last_error, 2)

    @property
    def integral(self) -> float:
        """Return the integral value."""
        return round(self._integral, 2)

    @property
    def derivative(self) -> float:
        """Return the derivative value."""
        if self._time_elapsed == 0 or len(self._derivative_errors) < 2:
            return 0

        derivative_error = (self._derivative_errors[-1] - self._derivative_errors[0]) / (len(self._derivative_errors) - 1)
        return round(self._kd * derivative_error / self._time_elapsed, 2)

    @property
    def output(self) -> float:
        """Return the control output value."""
        return sum(self._outputs) / len(self._outputs)

    @property
    def integral_enabled(self) -> bool:
        """Return whether the updates of the integral are enabled."""
        return self._integral_enabled

    @property
    def autotune_enabled(self) -> bool:
        """Return a boolean indicating whether autotune is enabled."""
        return self._autotune_enabled

    @property
    def num_autotune_outputs(self) -> int:
        """Return the number of updates collected."""
        return len(self._autotune_outputs)

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
