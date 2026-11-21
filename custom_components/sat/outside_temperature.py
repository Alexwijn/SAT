"""Outside temperature from sensors or weather entities."""
from __future__ import annotations

import logging
from typing import Optional

from homeassistant.components import sensor, weather
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State

from .entry_data import SensorsConfig
from .helpers import ensure_list, float_value, is_state_stale, state_age_seconds

_LOGGER = logging.getLogger(__name__)


class OutsideTemperature:
    """The outside temperature according to the first usable source, sensors before weather entities."""

    def __init__(self, config: SensorsConfig) -> None:
        self._max_age_seconds = config.sensor_max_value_age_seconds
        self._sources = sorted(ensure_list(config.outside_sensor_entity_id), key=lambda entity_id: sensor.DOMAIN not in entity_id)

    def read(self, hass: HomeAssistant) -> Optional[float]:
        """Return the temperature of the first usable source."""
        for entity_id in self._sources:
            state = hass.states.get(entity_id)

            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                continue

            if is_state_stale(state, self._max_age_seconds):
                _LOGGER.debug("Outside sensor %s stale (age=%.1fs > %.1fs)", entity_id, state_age_seconds(state), self._max_age_seconds)
                continue

            if (value := self._value(state)) is not None:
                return value

        return None

    @staticmethod
    def _value(state: State) -> Optional[float]:
        if sensor.DOMAIN in state.entity_id:
            return float_value(state.state)

        if weather.DOMAIN in state.entity_id:
            return float_value(state.attributes.get(ATTR_TEMPERATURE))

        return None
