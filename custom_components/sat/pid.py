"""PID controller logic for supply-air temperature tuning."""

import logging
from typing import Any, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import *
from .entry_data import PidConfig
from .heating_curve import HeatingCurve
from .helpers import float_value, timestamp as _timestamp, clamp_to_range
from .temperature_state import TemperatureState

_LOGGER = logging.getLogger(__name__)
timestamp = _timestamp  # keep public name for tests

DERIVATIVE_ALPHA1 = 0.8
DERIVATIVE_ALPHA2 = 0.6
DERIVATIVE_RAW_CAP = 5.0

SENSOR_MAX_INTERVAL = 900.0

STORAGE_VERSION = 1
STORAGE_KEY_INTEGRAL = "integral"
STORAGE_KEY_LAST_ERROR = "last_error"
STORAGE_KEY_RAW_DERIVATIVE = "raw_derivative"
STORAGE_KEY_LAST_TEMPERATURE = "last_temperature"
STORAGE_KEY_LAST_INTERVAL_UPDATED = "last_interval_updated"
STORAGE_KEY_LAST_DERIVATIVE_UPDATED = "last_derivative_updated"


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, heating_system: HeatingSystem, config: PidConfig, heating_curve: HeatingCurve) -> None:
        self._config = config
        self._heating_curve = heating_curve
        self._heating_system = heating_system

        self._raw_derivative: float = 0.0
        self._last_temperature: Optional[float] = None
        self._last_derivative_updated: Optional[float] = None

        self._store: Optional[Store] = None
        self._entity_id: Optional[str] = None
        self._hass: Optional[HomeAssistant] = None

        self.reset()

    @property
    def available(self):
        """Return whether the PID controller is available."""
        return self._last_error is not None and self._heating_curve.value is not None

    @property
    def kp(self) -> Optional[float]:
        """Return the value of kp based on the current configuration."""
        if not self._config.automatic_gains:
            return float(self._config.proportional)

        if self._heating_curve.value is None:
            return 0.0

        automatic_gain_value = 4 if self._heating_system == HeatingSystem.UNDERFLOOR else 3
        return round((self._config.heating_curve_coefficient * self._heating_curve.value) / automatic_gain_value, 6)

    @property
    def ki(self) -> Optional[float]:
        """Return the value of ki based on the current configuration."""
        if not self._config.automatic_gains:
            return float(self._config.integral)

        return round(self.kp / 8400, 6)

    @property
    def kd(self) -> Optional[float]:
        """Return the value of kd based on the current configuration."""
        if not self._config.automatic_gains:
            return float(self._config.derivative)

        return round(0.07 * 8400 * self.kp, 6)

    @property
    def proportional(self) -> float:
        """Return the proportional value."""
        if self.kp is None or self._last_error is None:
            return 0.0

        return round(self.kp * self._last_error, 3)

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
        if (heating_curve := self._heating_curve.value) is None:
            return 0.0

        return round(heating_curve + self.proportional + self.integral + self.derivative, 1)

    def reset(self) -> None:
        """Reset the PID controller to a clean state."""
        self._integral: float = 0.0
        self._last_error: Optional[float] = None
        self._last_interval_updated: Optional[float] = None

    async def async_added_to_hass(self, hass: HomeAssistant, entity_id: str, device_id: str) -> None:
        """Restore PID controller state from storage when the integration loads."""
        self._hass = hass
        self._entity_id = entity_id
        self._store = Store(hass, STORAGE_VERSION, f"sat.pid.{entity_id}.{device_id}")

        if not (data := await self._store.async_load()):
            return

        self._last_error = float_value(data.get(STORAGE_KEY_LAST_ERROR))
        self._integral = float(data.get(STORAGE_KEY_INTEGRAL, self._integral))
        self._last_temperature = float_value(data.get(STORAGE_KEY_LAST_TEMPERATURE))
        self._raw_derivative = float(data.get(STORAGE_KEY_RAW_DERIVATIVE, self._raw_derivative))

        if STORAGE_KEY_LAST_INTERVAL_UPDATED in data:
            value = data[STORAGE_KEY_LAST_INTERVAL_UPDATED]
            self._last_interval_updated = float(value) if value is not None else None

        if STORAGE_KEY_LAST_DERIVATIVE_UPDATED in data:
            value = data[STORAGE_KEY_LAST_DERIVATIVE_UPDATED]
            self._last_derivative_updated = float(value) if value is not None else None

        _LOGGER.debug("Loaded PID state from storage for entity=%s", self._entity_id)

    def set_heating_curve_value(self, heating_curve_value: float) -> None:
        """Set the heating curve value."""
        self._heating_curve_value = heating_curve_value

    def update(self, state: TemperatureState) -> None:
        """Update PID state with the latest error and heating curve value."""
        if self._heating_curve.value is None:
            _LOGGER.debug("Skipping PID update for %s because heating curve has no value", self._entity_id)
            return

        self._update_integral(state)
        self._update_derivative(state)

        self._last_error = state.error
        self._last_temperature = state.current

        if self._hass is not None:
            _LOGGER.debug(
                "PID update: entity=%s temperature=%.3f error=%.3f heating_curve=%.3f proportional=%.3f integral=%.3f derivative=%.3f output=%.3f",
                self._entity_id, state.current, state.error, self._heating_curve.value, self.proportional, self.integral, self.derivative, self.output
            )

            if self._store is not None:
                self._hass.create_task(self._async_save_state())

            self._hass.loop.call_soon_threadsafe(async_dispatcher_send, self._hass, SIGNAL_PID_UPDATED, self._entity_id)

    def _update_integral(self, state: TemperatureState) -> None:
        """Update the integral value in the PID controller."""
        error_abs = abs(state.error)
        state_timestamp = state.last_updated.timestamp()

        # Start a fresh time base when we enter the deadband.
        if self._last_error is not None and abs(self._last_error) > DEADBAND >= error_abs:
            self._last_interval_updated = state_timestamp

        # Reset integral outside the deadband so it only accumulates inside.
        if error_abs > DEADBAND:
            self._integral = 0.0
            self._last_interval_updated = state_timestamp
            return

        # Skip integration when integral gain is disabled.
        if self.ki is None:
            return

        # Ignore non-forward timestamps.
        if self._last_interval_updated is None:
            self._last_interval_updated = state_timestamp
            return

        if (delta_time := state_timestamp - self._last_interval_updated) <= 0:
            self._last_interval_updated = state_timestamp
            return

        # Cap the integration interval so long gaps don't over-accumulate.
        delta_time = min(delta_time, SENSOR_MAX_INTERVAL)
        self._integral += self.ki * state.error * delta_time

        # Clamp integral to the heating curve bounds.
        self._integral = clamp_to_range(self._integral, self._heating_curve.value)

        # Record the timestamp used for this integration step.
        self._last_interval_updated = state_timestamp

    def _update_derivative(self, state: TemperatureState) -> None:
        """Update the derivative term of the PID controller based on temperature slope."""
        error_abs = abs(state.error)
        state_timestamp = state.last_updated.timestamp()

        in_deadband = error_abs <= DEADBAND
        last_derivative_updated = self._last_derivative_updated
        has_last_temperature = self._last_temperature is not None
        is_forward = last_derivative_updated is not None and state_timestamp > last_derivative_updated

        # Bail out when we are in the deadband or lack valid forward temperature data.
        if in_deadband or not has_last_temperature or not is_forward:
            self._last_derivative_updated = state_timestamp
            return

        # Ignore updates when the sensor gap is too large.
        if (delta_time := state_timestamp - last_derivative_updated) > SENSOR_MAX_INTERVAL:
            self._last_derivative_updated = state_timestamp
            return

        # Convert the temperature delta into a time-based derivative.
        temperature_delta = state.current - self._last_temperature
        derivative = -temperature_delta / delta_time

        # Apply the first low-pass filter.
        filtered_derivative = DERIVATIVE_ALPHA1 * derivative + (1 - DERIVATIVE_ALPHA1) * self._raw_derivative

        # Apply the second low-pass filter and clamp the magnitude.
        self._raw_derivative = DERIVATIVE_ALPHA2 * filtered_derivative + (1 - DERIVATIVE_ALPHA2) * self._raw_derivative
        self._raw_derivative = max(-DERIVATIVE_RAW_CAP, min(self._raw_derivative, DERIVATIVE_RAW_CAP))

        self._last_derivative_updated = state_timestamp

        _LOGGER.debug(
            "Derivative update: entity=%s temperature=%.3f previous_temperature=%.3f raw_derivative=%.6f delta_time=%.3f",
            self._entity_id, state.current, self._last_temperature, self._raw_derivative, delta_time
        )

    async def _async_save_state(self) -> None:
        if self._store is None:
            return

        data: dict[str, Any] = {
            STORAGE_KEY_INTEGRAL: self._integral,
            STORAGE_KEY_LAST_ERROR: self._last_error,
            STORAGE_KEY_RAW_DERIVATIVE: self._raw_derivative,
            STORAGE_KEY_LAST_TEMPERATURE: self._last_temperature,
            STORAGE_KEY_LAST_INTERVAL_UPDATED: self._last_interval_updated,
            STORAGE_KEY_LAST_DERIVATIVE_UPDATED: self._last_derivative_updated,
        }

        await self._store.async_save(data)
