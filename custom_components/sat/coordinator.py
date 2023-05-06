from __future__ import annotations

import logging
import typing
from enum import Enum

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .config_store import SatConfigStore
from .const import *

if typing.TYPE_CHECKING:
    from .climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


class DeviceState(Enum):
    ON = "on"
    OFF = "off"


class SatDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, store: SatConfigStore) -> None:
        """Initialize."""
        self._store = store
        self._device_state = DeviceState.OFF
        self._simulation = bool(self._store.options.get(CONF_SIMULATION))
        self._heating_system = str(self._store.options.get(CONF_HEATING_SYSTEM))

        super().__init__(hass, _LOGGER, name=DOMAIN)

    @property
    def store(self):
        return self._store

    @property
    def supports_setpoint_management(self):
        return False

    @property
    def device_state(self):
        return self._device_state

    @property
    def maximum_setpoint(self) -> float:
        if self._heating_system == HEATING_SYSTEM_RADIATOR_HIGH_TEMPERATURES:
            return 75.0

        if self._heating_system == HEATING_SYSTEM_RADIATOR_MEDIUM_TEMPERATURES:
            return 65.0

        if self._heating_system == HEATING_SYSTEM_RADIATOR_LOW_TEMPERATURES:
            return 55.0

        if self._heating_system == HEATING_SYSTEM_UNDERFLOOR:
            return 50.0

    async def async_added_to_hass(self, climate: SatClimate) -> None:
        pass

    async def async_control_heating(self, climate: SatClimate, _time=None) -> None:
        pass

    async def async_control_setpoint(self, value: float) -> None:
        if self.supports_setpoint_management:
            self.logger.info("Set control setpoint to %d", value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        self._device_state = state
        self.logger.info("Set central heater state %s", state)
