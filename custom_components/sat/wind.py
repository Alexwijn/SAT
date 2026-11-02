"""Wind speed from wind sensors or weather entities."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import mean
from typing import Deque, Optional

from homeassistant.components import sensor, weather
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfSpeed
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import SpeedConverter

from .entry_data import SensorsConfig
from .helpers import ensure_list

WIND_SMOOTHING_WINDOW_SECONDS = 3600.0
WIND_SPEED_UNITS = frozenset(unit.value for unit in UnitOfSpeed)


@dataclass(frozen=True, slots=True)
class WindReading:
    """Wind speed in km/h and the entity that reported it."""

    speed: float
    entity_id: str


@dataclass(frozen=True, slots=True)
class WindObservation:
    timestamp: float
    reading: WindReading


class WindMonitor:
    """Read the wind speed from the first usable source and smooth it over the last hour."""

    def __init__(self, config: SensorsConfig) -> None:
        self._entity_ids = self.sources(config)
        self._max_age_seconds = config.sensor_max_value_age_seconds
        self._observations: Deque[WindObservation] = deque()

    @staticmethod
    def sources(config: SensorsConfig) -> list[str]:
        """Return the wind sources in order of preference."""
        entity_ids = config.wind_sensor_entity_id or [entity_id for entity_id in ensure_list(config.outside_sensor_entity_id) if entity_id.startswith(f"{weather.DOMAIN}.")]

        return sorted(entity_ids, key=lambda entity_id: not entity_id.startswith(f"{sensor.DOMAIN}."))

    @property
    def reading(self) -> Optional[WindReading]:
        """Return the mean wind speed of the current source over the smoothing window."""
        if not self._observations:
            return None

        entity_id = self._observations[-1].reading.entity_id
        speeds = [observation.reading.speed for observation in self._observations if observation.reading.entity_id == entity_id]

        return WindReading(speed=round(mean(speeds), 1), entity_id=entity_id)

    def observe(self, hass: HomeAssistant, now: float) -> None:
        """Record the current wind speed of the first usable source."""
        if (reading := self._read(hass)) is not None:
            self._observations.append(WindObservation(timestamp=now, reading=reading))

        while self._observations and self._observations[0].timestamp < (now - WIND_SMOOTHING_WINDOW_SECONDS):
            self._observations.popleft()

    def _read(self, hass: HomeAssistant) -> Optional[WindReading]:
        for entity_id in self._entity_ids:
            state = hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE) or self._is_stale(state):
                continue

            if (speed := self._speed(state)) is not None:
                return WindReading(speed=speed, entity_id=entity_id)

        return None

    def _is_stale(self, state: State) -> bool:
        return self._max_age_seconds > 0 and (dt_util.utcnow() - state.last_reported).total_seconds() > self._max_age_seconds

    @staticmethod
    def _speed(state: State) -> Optional[float]:
        if state.domain == weather.DOMAIN:
            value, unit = state.attributes.get(weather.ATTR_WEATHER_WIND_SPEED), state.attributes.get(weather.ATTR_WEATHER_WIND_SPEED_UNIT)
        else:
            value, unit = state.state, state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        if unit not in WIND_SPEED_UNITS:
            return None

        try:
            return SpeedConverter.convert(float(value), unit, UnitOfSpeed.KILOMETERS_PER_HOUR)
        except (TypeError, ValueError):
            return None
