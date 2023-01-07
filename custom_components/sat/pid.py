import logging
import time
from typing import Optional

from homeassistant.core import State

_LOGGER = logging.getLogger(__name__)


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, kp: float, ki: float, kd: float, sample_time_limit: Optional[float] = None):
        """Initialize the PID controller.

        Parameters:
        kp: The proportional gain of the PID controller.
        ki: The integral gain of the PID controller.
        kd: The derivative gain of the PID controller.
        sample_time_limit: The minimum time interval between updates to the PID controller, in seconds.
        """
        self._kp = kp
        self._ki = ki
        self._kd = kd
        self._sample_time_limit = sample_time_limit
        self.reset()

    def reset(self):
        """Reset the PID controller."""
        self._last_error = 0
        self._time_elapsed = 0
        self._previous_error = 0
        self._last_updated = time.time()

        self._integral = 0
        self._integral_enabled = True

    def enable_integral(self, enabled: bool):
        """Enable or disable the updates of the integral.

        Parameters:
        enabled: A boolean indicating whether to enable (True) or disable (False) the updates of the integral.
        """
        if self._integral_enabled != enabled:
            # Reset the integral if the enabled status changes
            self._integral = 0

        self._integral_enabled = enabled

    def update(self, error: float):
        """Update the PID controller.

        Parameters:
        error: The error value for the PID controller to use in the update.
        """
        current_time = time.time()
        time_elapsed = current_time - self._last_updated

        if error == self._last_error:
            _LOGGER.warning("Same error value detected")
            return

        if self._sample_time_limit and time_elapsed < self._sample_time_limit:
            _LOGGER.warning("Sample time limited")
            return

        self._last_updated = current_time
        self._time_elapsed = time_elapsed

        if self._integral_enabled:
            self._integral += error * time_elapsed

        self._previous_error = self._last_error
        self._last_error = error

    def update_reset(self, error: float):
        """Update the PID controller with resetting.

        Parameters:
        error: The error value for the PID controller to use in the update.
        """
        self._integral = 0

        self._time_elapsed = 0
        self._previous_error = 0

        self._last_error = error
        self._last_updated = time.time()

    def restore(self, state: State):
        """Restore the PID controller from a saved state.

        Parameters:
        state: The saved state of the PID controller to restore from.
        """
        if last_error := state.attributes.get("error"):
            self._last_error = last_error

        if last__integral := state.attributes.get("integral"):
            self._integral = last__integral

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
        return round(self._kp * self._last_error, 1)

    @property
    def integral(self) -> float:
        """Return the integral value."""
        if self._time_elapsed == 0:
            return 0

        return round(self._ki * self._integral * self._time_elapsed, 1)

    @property
    def derivative(self) -> float:
        """Return the derivative value."""
        if self._time_elapsed == 0:
            return 0

        return round(self._kd * (self._last_error - self._previous_error) / self._time_elapsed, 1)

    @property
    def output(self) -> float:
        """Return the control output value."""
        return self.proportional + self.integral + self.derivative

    @property
    def integral_enabled(self) -> bool:
        """Return whether the updates of the integral are enabled."""
        return self._integral_enabled
