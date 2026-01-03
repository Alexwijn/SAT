"""PID controller logic for supply-air temperature tuning."""

import logging
from typing import Optional, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import *
from .helpers import timestamp

_LOGGER = logging.getLogger(__name__)

DERIVATIVE_ALPHA1 = 0.8
DERIVATIVE_ALPHA2 = 0.6
DERIVATIVE_DECAY = 0.9
DERIVATIVE_RAW_CAP = 5.0
DERIVATIVE_MIN_INTERVAL = 30.0
DERIVATIVE_ERROR_ALPHA = 0.3
ERROR_EPSILON = 0.01

STORAGE_VERSION = 1
STORAGE_KEY_INTEGRAL = "integral"
STORAGE_KEY_LAST_ERROR = "last_error"
STORAGE_KEY_PREVIOUS_ERROR = "previous_error"
STORAGE_KEY_FILTERED_ERROR = "filtered_error"
STORAGE_KEY_RAW_DERIVATIVE = "raw_derivative"
STORAGE_KEY_LAST_INTERVAL_UPDATED = "last_interval_updated"
STORAGE_KEY_LAST_DERIVATIVE_UPDATED = "last_derivative_updated"


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, entity_id: str, heating_system: str, automatic_gain_value: float, heating_curve_coefficient: float, kp: float, ki: float, kd: float, automatic_gains: bool = False) -> None:
        self._kp: float = kp
        self._ki: float = ki
        self._kd: float = kd
        self._heating_system: str = heating_system
        self._automatic_gains: bool = automatic_gains
        self._automatic_gains_value: float = automatic_gain_value
        self._heating_curve_coefficient: float = heating_curve_coefficient

        self._entity_id: str = entity_id
        self._store: Optional[Store] = None
        self._hass: Optional[HomeAssistant] = None

        self.reset()

    @property
    def available(self):
        """Return whether the PID controller is available."""
        return self._last_error is not None and self._heating_curve is not None

    @property
    def kp(self) -> Optional[float]:
        """Return the value of kp based on the current configuration."""
        if not self._automatic_gains:
            return float(self._kp)

        if self._heating_curve is None:
            return 0.0

        automatic_gain_value = 4 if self._heating_system == HEATING_SYSTEM_UNDERFLOOR else 3
        return round((self._heating_curve_coefficient * self._heating_curve) / automatic_gain_value, 6)

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
    def last_error(self) -> Optional[float]:
        """Return the last error value."""
        return self._last_error

    @property
    def output(self) -> float:
        """Return the control output value."""
        if self._heating_curve is None:
            return 0.0

        return round(self._heating_curve + self.proportional + self.integral + self.derivative, 1)

    def reset(self) -> None:
        """Reset the PID controller to a clean state."""
        now = timestamp()

        self._last_interval_updated: float = now
        self._last_derivative_updated: float = now

        self._last_error: Optional[float] = None
        self._previous_error: Optional[float] = None
        self._filtered_error: Optional[float] = None
        self._heating_curve: Optional[float] = None

        # Reset integral and derivative accumulators.
        self._integral: float = 0.0
        self._raw_derivative: float = 0.0

    async def async_added_to_hass(self, hass: HomeAssistant, device_id: str) -> None:
        """Restore PID controller state from storage when the integration loads."""
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, f"sat.pid.{self._entity_id}.{device_id}")

        data: Optional[dict[str, Any]] = await self._store.async_load()
        if not data:
            return

        self._integral = float(data.get(STORAGE_KEY_INTEGRAL, self._integral))
        self._last_error = self._maybe_float(data.get(STORAGE_KEY_LAST_ERROR))
        self._previous_error = self._maybe_float(data.get(STORAGE_KEY_PREVIOUS_ERROR))
        self._filtered_error = self._maybe_float(data.get(STORAGE_KEY_FILTERED_ERROR))
        self._raw_derivative = float(data.get(STORAGE_KEY_RAW_DERIVATIVE, self._raw_derivative))

        self._last_interval_updated = float(data.get(STORAGE_KEY_LAST_INTERVAL_UPDATED, self._last_interval_updated))
        self._last_derivative_updated = float(data.get(STORAGE_KEY_LAST_DERIVATIVE_UPDATED, self._last_derivative_updated))

        _LOGGER.debug("Loaded PID state from storage for entity=%s", self._entity_id)

    def update(self, error: float, now: float, heating_curve: float) -> None:
        """Update PID state with the latest error and heating curve value."""
        self._heating_curve = heating_curve

        self._update_derivative(error, now)
        self._update_integral(error, now, heating_curve)

        if self._last_error is not None:
            self._previous_error = self._last_error

        self._last_error = error

        _LOGGER.debug(
            "PID update for %s (error=%.3f curve=%.3f proportional=%.3f integral=%.3f derivative=%.3f output=%.3f)",
            self._entity_id, error, heating_curve, self.proportional, self.integral, self.derivative, self.output
        )

        if self._hass is not None and self._store is not None:
            self._hass.create_task(self._async_save_state())

    def _update_integral(self, error: float, now: float, heating_curve_value: float) -> None:
        """Update the integral value in the PID controller."""

        # Reset the time base if we just entered the deadband.
        if self._last_error is not None and abs(self._last_error) > DEADBAND >= abs(error):
            self._last_interval_updated = now

        # Ensure the integral term is enabled for the current error.
        if abs(error) > DEADBAND:
            self._integral = 0.0
            return

        # Check if integral gain is set.
        if self.ki is None:
            return

        # Update the integral value.
        delta_time = now - self._last_interval_updated
        self._integral += self.ki * error * delta_time

        # Clamp integral to avoid pushing beyond the curve bounds.
        self._integral = min(self._integral, float(+heating_curve_value))
        self._integral = max(self._integral, float(-heating_curve_value))

        # Record the time of the latest update.
        self._last_interval_updated = now

    def _update_derivative(self, error: float, now: float) -> None:
        """Update the derivative term of the PID controller based on filtered error."""
        if self._filtered_error is None:
            self._filtered_error = error
            return

        error_changed = self._last_error is None or abs(error - self._last_error) >= ERROR_EPSILON
        filtered_error = DERIVATIVE_ERROR_ALPHA * error + (1 - DERIVATIVE_ERROR_ALPHA) * self._filtered_error

        if abs(error) <= DEADBAND:
            self._filtered_error = filtered_error
            self._raw_derivative *= DERIVATIVE_DECAY
            return

        if not error_changed:
            self._filtered_error = filtered_error
            return

        time_elapsed = now - self._last_derivative_updated
        if time_elapsed <= 0:
            self._filtered_error = filtered_error
            return

        # Basic derivative: slope between current and last filtered error.
        derivative = (filtered_error - self._filtered_error) / time_elapsed

        # First low-pass filter.
        filtered_derivative = DERIVATIVE_ALPHA1 * derivative + (1 - DERIVATIVE_ALPHA1) * self._raw_derivative

        # Second low-pass filter.
        self._raw_derivative = DERIVATIVE_ALPHA2 * filtered_derivative + (1 - DERIVATIVE_ALPHA2) * self._raw_derivative
        self._raw_derivative = max(-DERIVATIVE_RAW_CAP, min(self._raw_derivative, DERIVATIVE_RAW_CAP))

        self._filtered_error = filtered_error
        self._last_derivative_updated = now

        _LOGGER.debug(
            "Derivative update: entity=%s error=%.3f filtered=%.3f raw=%.6f dt=%.3f changed=%s",
            self._entity_id, error, filtered_error, self._raw_derivative, time_elapsed, error_changed
        )

    @staticmethod
    def _maybe_float(value: Any) -> Optional[float]:
        if value is None:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def _async_save_state(self) -> None:
        if self._store is None:
            return

        data: dict[str, Any] = {
            STORAGE_KEY_INTEGRAL: self._integral,
            STORAGE_KEY_LAST_ERROR: self._last_error,
            STORAGE_KEY_PREVIOUS_ERROR: self._previous_error,
            STORAGE_KEY_FILTERED_ERROR: self._filtered_error,
            STORAGE_KEY_RAW_DERIVATIVE: self._raw_derivative,
            STORAGE_KEY_LAST_INTERVAL_UPDATED: self._last_interval_updated,
            STORAGE_KEY_LAST_DERIVATIVE_UPDATED: self._last_derivative_updated,
        }

        await self._store.async_save(data)
        _LOGGER.debug("Saved PID state to storage for entity=%s", self._entity_id)
