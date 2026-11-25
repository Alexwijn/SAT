"""PID controller logic for supply-air temperature tuning."""

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import *
from .entry_data import PidConfig, SatConfig
from .heating_curve import HeatingCurve
from .helpers import float_value, to_float, clamp, clamp_to_range
from .temperature.state import TemperatureState
from .types import HeatingSystem

_LOGGER = logging.getLogger(__name__)

DERIVATIVE_RAW_CAP = 10.0 / 3600.0
PID_UPDATE_INTERVAL = 60

DERIVATIVE_MINIMUM_SAMPLES = 3
DERIVATIVE_MINIMUM_SPAN_RATIO = 0.5
DERIVATIVE_DEFAULT_SENSOR_STEP = 0.1
DERIVATIVE_WINDOW_MINIMUM_SECONDS = 900
DERIVATIVE_WINDOW_MAXIMUM_SECONDS = 1800
DERIVATIVE_WINDOW_SECONDS_PER_DEGREE = 12000

STORAGE_VERSION = 1
STORAGE_KEY_SAMPLES = "samples"
STORAGE_KEY_SENSOR_STEP = "sensor_step"
STORAGE_KEY_INTEGRAL = "integral"
STORAGE_KEY_LAST_ERROR = "last_error"
STORAGE_KEY_RAW_DERIVATIVE = "raw_derivative"


@dataclass(frozen=True, slots=True)
class TemperatureSample:
    timestamp: float
    temperature: float


class PID:
    """A proportional-integral-derivative (PID) controller."""

    def __init__(self, heating_system: HeatingSystem, heating_curve: HeatingCurve, config: PidConfig) -> None:
        self._config: PidConfig = config
        self._heating_curve: HeatingCurve = heating_curve
        self._heating_system: HeatingSystem = heating_system

        self._integral: float = 0.0
        self._last_error: Optional[float] = None

        self._raw_derivative: float = 0.0
        self._samples: Deque[TemperatureSample] = deque()
        self._sensor_step: Optional[float] = None

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
        return round(self._raw_derivative, 6)

    @property
    def sensor_step(self) -> float:
        """The smallest change the sensor has reported, which sets how long a slope must be observed."""
        return self._sensor_step if self._sensor_step is not None else DERIVATIVE_DEFAULT_SENSOR_STEP

    @property
    def derivative_window_seconds(self) -> int:
        """Return the slope window, long enough for one sensor step to barely move the derivative."""
        return int(clamp(self.sensor_step * DERIVATIVE_WINDOW_SECONDS_PER_DEGREE, DERIVATIVE_WINDOW_MINIMUM_SECONDS, DERIVATIVE_WINDOW_MAXIMUM_SECONDS))

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
        self._raw_derivative = 0.0

        _LOGGER.info("Reset PID controller for %s", self._entity_id)

    def forget_samples(self) -> None:
        """Forget the samples, as they came from another sensor."""
        self._samples = deque()
        self._sensor_step = None

    async def async_added_to_hass(self, hass: HomeAssistant, entity_id: str, device_id: str) -> None:
        """Restore PID controller state from storage when the integration loads."""
        self._hass = hass
        self._entity_id = entity_id
        self._store = self._store_for(entity_id, device_id)

        if not (data := await self._store.async_load()):
            return

        self._last_error = float_value(data.get(STORAGE_KEY_LAST_ERROR))
        self._sensor_step = float_value(data.get(STORAGE_KEY_SENSOR_STEP))

        self._integral = to_float(data.get(STORAGE_KEY_INTEGRAL), self._integral)
        self._raw_derivative = clamp_to_range(to_float(data.get(STORAGE_KEY_RAW_DERIVATIVE), self._raw_derivative), DERIVATIVE_RAW_CAP)

        for item in data.get(STORAGE_KEY_SAMPLES, []):
            try:
                self._samples.append(TemperatureSample(timestamp=float(item[0]), temperature=float(item[1])))
            except (TypeError, ValueError, IndexError):
                continue

        _LOGGER.debug("Loaded PID state from storage for entity=%s", self._entity_id)

    async def async_rename(self, entity_id: str, device_id: str) -> None:
        """Move the stored state along with the new id of its entity."""
        if self._hass is None or entity_id == self._entity_id:
            return

        previous_store = self._store

        self._entity_id = entity_id
        self._store = self._store_for(entity_id, device_id)
        await self._async_save_state()

        if previous_store is not None:
            await previous_store.async_remove()

    def update(self, state: TemperatureState, freeze_integral: bool = False) -> None:
        """Update PID state with the latest error and heating curve value."""
        if self._heating_curve.value is None:
            _LOGGER.debug("Skipping PID update for %s because heating curve has no value", self._entity_id)
            return

        self._update_derivative(state)

        if not freeze_integral:
            self._update_integral(state)

        self._last_error = state.error

        _LOGGER.debug(
            "PID update: entity=%s current_temperature=%.3f setpoint=%.3f heating_curve=%.3f freeze_integral=%s P=%.3f I=%.3f D=%.3f output=%.3f",
            self._entity_id, state.current, state.setpoint, self._heating_curve.value,
            freeze_integral, self.proportional, self.integral, self.derivative, self.output
        )

        if self._hass is not None:
            if self._store is not None:
                self._hass.create_task(self._async_save_state())

            self._hass.loop.call_soon_threadsafe(async_dispatcher_send, self._hass, SIGNAL_PID_UPDATED, self._entity_id)

    def _update_integral(self, state: TemperatureState) -> None:
        """Accumulate the integral inside the deadband and hold it outside, so a short excursion keeps the learned bias."""
        if abs(state.error) > DEADBAND:
            return

        self._integral += self.ki * state.error * PID_UPDATE_INTERVAL
        self._integral = clamp_to_range(self._integral, self._heating_curve.value)

        _LOGGER.debug(
            "PID integral update: entity=%s current_temperature=%.3f target_temperature=%.3f error=%.3f value=%.6f",
            self._entity_id, state.current, state.setpoint, state.error, self._integral
        )

    def _update_derivative(self, state: TemperatureState) -> None:
        """Derive the error slope from a least-squares fit over the recent samples."""
        self._record_sample(state)

        window = self.derivative_window_seconds
        latest = self._samples[-1].timestamp
        samples = [sample for sample in self._samples if latest - sample.timestamp <= window]

        if len(samples) < DERIVATIVE_MINIMUM_SAMPLES or (latest - samples[0].timestamp) < DERIVATIVE_MINIMUM_SPAN_RATIO * window:
            return

        self._raw_derivative = clamp_to_range(-self._slope(samples), DERIVATIVE_RAW_CAP)

        _LOGGER.debug(
            "PID derivative update: entity=%s samples=%d window=%ds sensor_step=%.3f raw_value=%.6f",
            self._entity_id, len(samples), window, self.sensor_step, self._raw_derivative,
        )

    def _record_sample(self, state: TemperatureState) -> None:
        timestamp = state.last_reported.timestamp()

        if self._samples and timestamp <= self._samples[-1].timestamp:
            return

        if self._samples and state.current != self._samples[-1].temperature:
            step = round(abs(state.current - self._samples[-1].temperature), 3)
            self._sensor_step = step if self._sensor_step is None else min(self._sensor_step, step)

        self._samples.append(TemperatureSample(timestamp=timestamp, temperature=state.current))

        while timestamp - self._samples[0].timestamp > DERIVATIVE_WINDOW_MAXIMUM_SECONDS:
            self._samples.popleft()

    @staticmethod
    def _slope(samples: list[TemperatureSample]) -> float:
        mean_timestamp = sum(sample.timestamp for sample in samples) / len(samples)
        mean_temperature = sum(sample.temperature for sample in samples) / len(samples)

        variance = sum((sample.timestamp - mean_timestamp) ** 2 for sample in samples)
        covariance = sum((sample.timestamp - mean_timestamp) * (sample.temperature - mean_temperature) for sample in samples)

        return covariance / variance if variance > 0 else 0.0

    def _store_for(self, entity_id: str, device_id: str) -> Store:
        return Store(self._hass, STORAGE_VERSION, f"sat.pid.{entity_id}.{device_id}")

    async def _async_save_state(self) -> None:
        if self._store is None:
            return

        data: dict[str, Any] = {
            STORAGE_KEY_INTEGRAL: self._integral,
            STORAGE_KEY_LAST_ERROR: self._last_error,
            STORAGE_KEY_RAW_DERIVATIVE: self._raw_derivative,
            STORAGE_KEY_SENSOR_STEP: self._sensor_step,
            STORAGE_KEY_SAMPLES: [[sample.timestamp, sample.temperature] for sample in self._samples],
        }

        await self._store.async_save(data)
