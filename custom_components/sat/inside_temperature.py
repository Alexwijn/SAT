"""Inside temperature of the primary room."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Optional

from homeassistant.components.climate import ATTR_CURRENT_TEMPERATURE, DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State

from .const import INSIDE_FALLBACK_DELAY_SECONDS
from .entry_data import SatConfig
from .helpers import float_value, is_state_stale, state_age_seconds, sustained_runtime
from .types import InsideSource

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InsideReading:
    """Inside temperature, where it came from and when it was reported."""

    temperature: float
    source: InsideSource
    origin: str
    last_reported: datetime
    last_updated: datetime
    last_changed: datetime


class InsideTemperature:
    """The inside temperature according to the configured sensor, or its fallbacks while that is lost."""

    def __init__(self, config: SatConfig) -> None:
        self._sensor = config.sensors.inside_sensor_entity_id
        self._max_age_seconds = config.sensors.sensor_max_value_age_seconds

        self._thermostat = config.thermostat
        self._radiators = config.radiators

        self._falling_back: bool = False
        self._missing_since: Optional[float] = None

    def observe(self, hass: HomeAssistant, now: float) -> None:
        """Fall back once the sensor has been lost for a while, and return to it at once."""
        if self._sensor is None or self._reading(hass, self._sensor, InsideSource.SENSOR) is not None:
            self._falling_back = False
            self._missing_since = None
            return

        runtime = sustained_runtime(now, self._missing_since)
        self._missing_since = runtime.started_at
        self._falling_back = runtime.elapsed_seconds >= INSIDE_FALLBACK_DELAY_SECONDS

    def read(self, hass: HomeAssistant) -> Optional[InsideReading]:
        """Return the reading of the sensor, or of its fallback while the sensor is lost."""
        if (reading := self._reading(hass, self._sensor, InsideSource.SENSOR)) is not None or not self._falling_back:
            return reading

        if (reading := self._reading(hass, self._thermostat, InsideSource.THERMOSTAT)) is not None:
            return reading

        return self._radiator_mean(hass)

    def _radiator_mean(self, hass: HomeAssistant) -> Optional[InsideReading]:
        readings = [reading for entity_id in self._radiators if (reading := self._reading(hass, entity_id, InsideSource.RADIATORS)) is not None]

        if not readings:
            return None

        return InsideReading(
            source=InsideSource.RADIATORS,
            origin=", ".join(reading.origin for reading in readings),
            temperature=round(mean(reading.temperature for reading in readings), 2),
            last_reported=max(reading.last_reported for reading in readings),
            last_updated=max(reading.last_updated for reading in readings),
            last_changed=max(reading.last_changed for reading in readings),
        )

    def _reading(self, hass: HomeAssistant, entity_id: Optional[str], source: InsideSource) -> Optional[InsideReading]:
        if entity_id is None or (state := hass.states.get(entity_id)) is None:
            return None

        if is_state_stale(state, self._max_age_seconds):
            _LOGGER.debug("Inside sensor %s stale (age=%.1fs > %.1fs)", entity_id, state_age_seconds(state), self._max_age_seconds)
            return None

        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None

        if (temperature := float_value(self._value(state))) is None:
            if source == InsideSource.SENSOR:
                _LOGGER.warning("Sensor %s has a non-numeric value %r", entity_id, self._value(state))

            return None

        return InsideReading(
            source=source,
            origin=entity_id,
            temperature=temperature,
            last_reported=state.last_reported,
            last_updated=state.last_updated,
            last_changed=state.last_changed,
        )

    @staticmethod
    def _value(state: State) -> Optional[str]:
        return state.attributes.get(ATTR_CURRENT_TEMPERATURE) if state.domain == CLIMATE_DOMAIN else state.state
