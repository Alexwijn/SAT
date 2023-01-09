import time
from typing import Optional

from homeassistant.core import State


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, kp: float, ki: float, kd: float, target_temp_tolerance: Optional[float] = 0.5, sample_time_limit: Optional[float] = 0):
        """Initialize the PID controller.

        Parameters:
        kp: The proportional gain of the PID controller.
        ki: The integral gain of the PID controller.
        kd: The derivative gain of the PID controller.
        target_temp_tolerance: The tolerance for the target temperature, in degrees.
        sample_time_limit: The minimum time interval between updates to the PID controller, in seconds.
        """
        self._kp = kp
        self._ki = ki
        self._kd = kd
        self._sample_time_limit = sample_time_limit
        self._target_temp_tolerance = target_temp_tolerance
        self.reset()

    def reset(self) -> None:
        """Reset the PID controller."""
        self._num_updates = 0
        self._last_error = 0
        self._time_elapsed = 0
        self._previous_error = 0
        self._last_updated = time.time()

        # Reset the integral
        self._integral = 0
        self._integral_enabled = True

        # Reset list of outputs
        self._outputs = []
        self._inside_temperatures = []
        self._outside_temperatures = []

        # Reset autotune flag
        self._autotune_enabled = False
        self._optimal_kp = None
        self._optimal_ki = None
        self._optimal_kd = None
        self._optimal_heating_curve = None
        self._optimal_heating_curve_move = None

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
        self._num_updates = 0

        self._outputs = []
        self._inside_temperatures = []
        self._outside_temperatures = []

        self._autotune_enabled = enabled

    def update(self, error: float, inside_temperature: float, outside_temperature: float, heating_curve_value: float) -> None:
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

        self._num_updates += 1
        self._last_updated = current_time
        self._time_elapsed = time_elapsed

        if self._integral_enabled:
            self._integral += self._ki * error * time_elapsed

        self._previous_error = self._last_error
        self._last_error = error

        if self._autotune_enabled:
            # Check if target temperature is reached
            if abs(error) < self._target_temp_tolerance:
                self.enable_autotune(False)
            elif self._autotune_enabled:
                self._inside_temperatures.append(inside_temperature)
                self._outside_temperatures.append(outside_temperature)
                self._outputs.append((self.output, heating_curve_value, time_elapsed))

    def update_reset(self, error: float) -> None:
        """Update the PID controller with resetting.

        Parameters:
        error: The error value for the PID controller to use in the update.
        """
        self._integral = 0

        self._time_elapsed = 0
        self._previous_error = 0

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

    def autotune(self) -> None:
        """Calculate and set the optimal PID gains and heating curve based on previous outputs and temperatures."""
        self.determine_optimal_pid_gains()
        self.determine_optimal_heating_curve()

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
        kp = round((1 - y_intercept) / slope, 2)
        ki = round(sum_integral / sum_output, 2)
        kd = round(slope / (1 - y_intercept), 2)

        # Store the latest autotune values
        self._optimal_kp = kp
        self._optimal_ki = ki
        self._optimal_kd = kd

    def determine_optimal_heating_curve(self) -> None:
        """Calculate the optimal heating curve and heating curve move.

        The heating curve is the temperature at which the heating system should turn on or off
        based on the outside temperature. The heating curve move is an offset applied to the
        heating curve to adjust the temperature at which the heating system should turn on or off.
        """
        # Check that the lists have the same length
        if len(self._outside_temperatures) != len(self._inside_temperatures):
            raise ValueError("The lists of outside temperatures and inside temperatures must have the same length")

        # Check that the lists have at least 2 elements
        if len(self._outside_temperatures) < 2:
            raise ValueError("The lists must have at least 2 elements")

        # Initialize variables
        sum_x = 0
        sum_y = 0
        sum_xx = 0
        sum_xy = 0

        # Iterate through the outside temperatures and setpoints
        for x, y in zip(self._outside_temperatures, self._inside_temperatures):
            sum_x += x
            sum_y += y
            sum_xx += x * x
            sum_xy += x * y

        # Calculate the slope and y-intercept
        n = len(self._outside_temperatures)

        # Handle the case where the outside temperature does not change
        if sum_xx == sum_x * sum_x:
            self._optimal_heating_curve = sum_y / n
            self._optimal_heating_curve_move = 0
        else:
            slope = (n * sum_xy - sum_x * sum_y) / (n * sum_xx - sum_x * sum_x)
            y_intercept = (sum_y - slope * sum_x) / n

            # Calculate the heating curve and heating curve move
            self._optimal_heating_curve = -y_intercept / slope
            self._optimal_heating_curve_move = y_intercept

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
    def proportional(self) -> float:
        """Return the proportional value."""
        return round(self._kp * self._last_error, 2)

    @property
    def integral(self) -> float:
        """Return the integral value."""
        if self._time_elapsed == 0:
            return 0

        return round(self._ki * self._integral * self._time_elapsed, 2)

    @property
    def derivative(self) -> float:
        """Return the derivative value."""
        if self._time_elapsed == 0:
            return 0

        return round(self._kd * (self._last_error - self._previous_error) / self._time_elapsed, 2)

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
    def num_updates(self) -> int:
        """Return the number of updates collected."""
        return self._num_updates

    @property
    def optimal_kp(self) -> float:
        """Return the optimal proportional gain."""
        return self._optimal_kp

    @property
    def optimal_ki(self) -> float:
        """Return the optimal integral gain."""
        return self._optimal_ki

    @property
    def optimal_heating_curve(self) -> float:
        """Return the optimal heating curve value."""
        return self._optimal_heating_curve

    @property
    def optimal_heating_curve_move(self) -> float:
        """Return the optimal heating curve move value."""
        return self._optimal_heating_curve_move

    @property
    def optimal_kd(self) -> float:
        """Return the optimal derivative gain."""
        return self._optimal_kd
