"""Inside temperature of the primary room."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from homeassistant.components.climate import ATTR_CURRENT_TEMPERATURE, DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State

from .entry_data import SatConfig
from .helpers import float_value, is_state_stale, state_age_seconds

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InsideReading:
    """Inside temperature and when it was reported."""

    temperature: float
    last_reported: datetime
    last_updated: datetime
    last_changed: datetime


class InsideTemperature:
    """The inside temperature according to the configured sensor."""

    def __init__(self, config: SatConfig) -> None:
        self._sensor = config.sensors.inside_sensor_entity_id
        self._max_age_seconds = config.sensors.sensor_max_value_age_seconds

    def read(self, hass: HomeAssistant) -> Optional[InsideReading]:
        """Return the reading of the sensor, if it has a usable one."""
        return self._reading(hass, self._sensor)

    def _reading(self, hass: HomeAssistant, entity_id: Optional[str]) -> Optional[InsideReading]:
        if entity_id is None or (state := hass.states.get(entity_id)) is None:
            return None

        if is_state_stale(state, self._max_age_seconds):
            _LOGGER.debug("Inside sensor %s stale (age=%.1fs > %.1fs)", entity_id, state_age_seconds(state), self._max_age_seconds)
            return None

        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None

        if (temperature := float_value(self._value(state))) is None:
            _LOGGER.warning("Sensor %s has a non-numeric value %r", entity_id, self._value(state))
            return None

        return InsideReading(temperature=temperature, last_reported=state.last_reported, last_updated=state.last_updated, last_changed=state.last_changed)

    @staticmethod
    def _value(state: State) -> Optional[str]:
        return state.attributes.get(ATTR_CURRENT_TEMPERATURE) if state.domain == CLIMATE_DOMAIN else state.state
