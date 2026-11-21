"""Wind speed from wind sensors or weather entities."""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from statistics import mean
from typing import Deque, Mapping, Optional

from homeassistant.components import sensor, weather
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfSpeed
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import SpeedConverter

from .const import DOMAIN
from .entry_data import SensorsConfig
from .helpers import ensure_list

_LOGGER = logging.getLogger(__name__)

RECORDER_DOMAIN = "recorder"

WIND_SMOOTHING_WINDOW_SECONDS = 3600.0
WIND_HISTORY_INTERVAL_SECONDS = 3600.0
WIND_HISTORY_STARTUP_DELAY_SECONDS = 60
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


class WindHistory:
    """Hourly mean wind speeds from the long-term statistics of the recorder."""

    def __init__(self, entry_id: str, config: SensorsConfig) -> None:
        self._entry_id = entry_id
        self._sensor_ids = [entity_id for entity_id in WindMonitor.sources(config) if entity_id.startswith(f"{sensor.DOMAIN}.")]

    @staticmethod
    def hour(timestamp: float) -> float:
        """Return the start of the hour that holds the timestamp."""
        return float(int(timestamp // WIND_HISTORY_INTERVAL_SECONDS) * WIND_HISTORY_INTERVAL_SECONDS)

    @staticmethod
    def hourly(rows: Mapping[str, list[dict]], statistic_ids: list[str]) -> dict[float, WindReading]:
        """Merge the hourly means, where an earlier statistic takes precedence over a later one."""
        readings: dict[float, WindReading] = {}

        for statistic_id in statistic_ids:
            for row in rows.get(statistic_id, []):
                if row.get("mean") is not None:
                    readings.setdefault(WindHistory.hour(row["start"]), WindReading(speed=round(row["mean"], 1), entity_id=statistic_id))

        return readings

    async def async_hourly(self, hass: HomeAssistant, start: float) -> dict[float, WindReading]:
        """Return the hourly mean wind speeds since the start, or nothing without a recorder."""
        if RECORDER_DOMAIN not in hass.config.components:
            return {}

        mirror_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{self._entry_id}-wind-speed")
        statistic_ids = [*self._sensor_ids, *([mirror_id] if mirror_id is not None else [])]

        if not statistic_ids:
            return {}

        try:
            rows = await self._async_load_statistics(hass, set(statistic_ids), start)
        except Exception as exception:
            _LOGGER.warning("Unable to load wind speed statistics: %s", exception)
            return {}

        return self.hourly(rows, statistic_ids)

    @staticmethod
    async def _async_load_statistics(hass: HomeAssistant, statistic_ids: set[str], start: float) -> dict[str, list[dict]]:
        # The recorder and its requirements may be absent, so they can only be imported once it is known to be loaded.
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        return await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            dt_util.utc_from_timestamp(WindHistory.hour(start)),
            None,
            statistic_ids,
            "hour",
            {SpeedConverter.UNIT_CLASS: UnitOfSpeed.KILOMETERS_PER_HOUR},
            {"mean"},
        )
