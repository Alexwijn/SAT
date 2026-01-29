"""PID controller logic for supply-air temperature tuning."""

import logging
from typing import Any, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import *
from .entry_data import PidConfig, SatConfig
from .heating_curve import HeatingCurve
from .helpers import float_value, timestamp as _timestamp, clamp_to_range
from .temperature.state import TemperatureState
from .types import HeatingSystem

_LOGGER = logging.getLogger(__name__)
timestamp = _timestamp  # keep public name for tests

DERIVATIVE_ALPHA1 = 0.8
DERIVATIVE_ALPHA2 = 0.6
DERIVATIVE_RAW_CAP = 5.0

STORAGE_VERSION = 1
STORAGE_KEY_INTEGRAL = "integral"
STORAGE_KEY_LAST_ERROR = "last_error"
STORAGE_KEY_RAW_DERIVATIVE = "raw_derivative"
STORAGE_KEY_LAST_TEMPERATURE = "last_temperature"
STORAGE_KEY_LAST_INTEGRAL_UPDATED = "last_integral_updated"
STORAGE_KEY_LAST_DERIVATIVE_UPDATED = "last_derivative_updated"


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, heating_system: HeatingSystem, heating_curve: HeatingCurve, config: PidConfig) -> None:
        self._config = config
        self._heating_curve = heating_curve
        self._heating_system = heating_system

        self._integral: float = 0.0
        self._last_error: Optional[float] = None
        self._last_integral_updated: Optional[float] = None

        self._raw_derivative: float = 0.0
        self._last_temperature: Optional[float] = None
        self._last_derivative_updated: Optional[float] = None

        self._store: Optional[Store] = None
        self._entity_id: Optional[str] = None
        self._hass: Optional[HomeAssistant] = None

        self.reset()

    @staticmethod
    def from_config(heating_curve: HeatingCurve, config: SatConfig):
        """Create an instance from configuration."""
        return PID(heating_curve=heating_curve, heating_system=config.heating_system, config=config.pid)

    @property
    def available(self) -> bool:
        """Return whether the PID controller is available."""
        return self._last_error is not None and self._heating_curve.value is not None

    @property
    def kp(self) -> Optional[float]:
        """Return the value of kp based on the current configuration."""
        if not self._config.automatic_gains:
            return float_value(self._config.proportional)

        if self._heating_curve.value is None:
            return 0.0

        automatic_gain_value = 4 if self._heating_system == HeatingSystem.UNDERFLOOR else 3
        return round((self._config.heating_curve_coefficient * self._heating_curve.value) / automatic_gain_value, 6)

    @property
    def ki(self) -> Optional[float]:
        """Return the value of ki based on the current configuration."""
        if not self._config.automatic_gains:
            return float(self._config.integral)

        if self.kp is None:
            return 0.0

        return round(self.kp / 8400, 6)

    @property
    def kd(self) -> Optional[float]:
        """Return the value of kd based on the current configuration."""
        if not self._config.automatic_gains:
            return float(self._config.derivative)

        if self.kp is None:
            return 0.0

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
        if (heating_curve_value := self._heating_curve.value) is None:
            return 0.0

        return round(heating_curve_value + self.proportional + self.integral + self.derivative, 1)

    def reset(self) -> None:
        """Reset the PID controller to a clean state."""
        self._integral = 0.0
        self._last_error = None
        self._last_integral_updated = None

    async def async_added_to_hass(self, hass: HomeAssistant, entity_id: str, device_id: str) -> None:
        """Restore PID controller state from storage when the integration loads."""
        self._hass = hass
        self._entity_id = entity_id
        self._store = Store(hass, STORAGE_VERSION, f"sat.pid.{entity_id}.{device_id}")

        if not (data := await self._store.async_load()):
            return

        self._last_error = float_value(data.get(STORAGE_KEY_LAST_ERROR))
        self._last_temperature = float_value(data.get(STORAGE_KEY_LAST_TEMPERATURE))
        self._last_derivative_updated = float_value(data.get(STORAGE_KEY_LAST_DERIVATIVE_UPDATED))

        self._integral = float(data.get(STORAGE_KEY_INTEGRAL, self._integral))
        self._last_integral_updated = float_value(data.get(STORAGE_KEY_LAST_INTEGRAL_UPDATED))
        self._raw_derivative = float(data.get(STORAGE_KEY_RAW_DERIVATIVE, self._raw_derivative))

        _LOGGER.debug("Loaded PID state from storage for entity=%s", self._entity_id)

    def update(self, state: TemperatureState) -> None:
        """Update PID state with the latest error and heating curve value."""
        if self._heating_curve.value is None:
            _LOGGER.debug("Skipping PID update for %s because heating curve has no value", self._entity_id)
            return

        self._update_derivative(state)
        self._update_integral(state)

        self._last_error = state.error
        self._last_temperature = state.current

        _LOGGER.debug(
            "PID update: entity=%s current_temperature=%.3f setpoint=%.3f heating_curve=%.3f P=%.3f I=%.3f D=%.3f output=%.3f",
            self._entity_id, state.current, state.setpoint, self._heating_curve.value,
            self.proportional, self.integral, self.derivative, self.output
        )

        if self._hass is not None:
            if self._store is not None:
                self._hass.create_task(self._async_save_state())

            self._hass.loop.call_soon_threadsafe(async_dispatcher_send, self._hass, SIGNAL_PID_UPDATED, self._entity_id)

    def _update_integral(self, state: TemperatureState) -> None:
        """Update the integral value in the PID controller."""
        if abs(state.error) > DEADBAND:
            self._integral = 0.0
            self._last_integral_updated = None
            return

        if self._last_integral_updated is None:
            self._last_integral_updated = state.last_reported.timestamp()
            return

        delta_time = state.last_reported.timestamp() - self._last_integral_updated

        # Ignore non-forward timestamps.
        if delta_time <= 0:
            self._last_integral_updated = state.last_reported.timestamp()
            return

        # Skip integration when integral gain is disabled.
        if self.ki is None:
            return

        self._integral += self.ki * state.error * delta_time
        self._integral = clamp_to_range(self._integral, self._heating_curve.value)

        # Record the timestamp used for this integration step.
        self._last_integral_updated = state.last_reported.timestamp()

    def _update_derivative(self, state: TemperatureState) -> None:
        """Update the derivative term of the PID controller based on temperature slope."""
        if self.kd is None:
            return

        if self._last_temperature is None or self._last_derivative_updated is None:
            self._last_temperature = state.current
            self._last_derivative_updated = state.last_changed.timestamp()
            return

        if abs(state.error) <= DEADBAND:
            self._last_temperature = state.current
            self._last_derivative_updated = state.last_changed.timestamp()
            return

        temperature_delta = state.current - self._last_temperature
        if temperature_delta == 0.0:
            self._last_temperature = state.current
            self._last_derivative_updated = state.last_changed.timestamp()
            return

        delta_time = state.last_changed.timestamp() - self._last_derivative_updated

        if delta_time <= 0:
            self._last_temperature = state.current
            self._last_derivative_updated = state.last_changed.timestamp()
            return

        derivative = -temperature_delta / delta_time

        # Apply the first low-pass filter.
        filtered_derivative = DERIVATIVE_ALPHA1 * derivative + (1 - DERIVATIVE_ALPHA1) * self._raw_derivative

        # Apply the second low-pass filter and clamp the magnitude.
        self._raw_derivative = DERIVATIVE_ALPHA2 * filtered_derivative + (1 - DERIVATIVE_ALPHA2) * self._raw_derivative
        self._raw_derivative = max(-DERIVATIVE_RAW_CAP, min(self._raw_derivative, DERIVATIVE_RAW_CAP))

        self._last_derivative_updated = state.last_changed.timestamp()

        _LOGGER.debug(
            "PID derivative update: entity=%s previous_temperature=%.3f current_temperature=%.3f raw_derivative=%.6f delta_time=%.3f",
            self._entity_id, self._last_temperature, state.current, self._raw_derivative, delta_time,
        )

    async def _async_save_state(self) -> None:
        if self._store is None:
            return

        data: dict[str, Any] = {
            STORAGE_KEY_INTEGRAL: self._integral,
            STORAGE_KEY_LAST_ERROR: self._last_error,
            STORAGE_KEY_RAW_DERIVATIVE: self._raw_derivative,
            STORAGE_KEY_LAST_TEMPERATURE: self._last_temperature,
            STORAGE_KEY_LAST_INTEGRAL_UPDATED: self._last_integral_updated,
            STORAGE_KEY_LAST_DERIVATIVE_UPDATED: self._last_derivative_updated,
        }

        await self._store.async_save(data)
