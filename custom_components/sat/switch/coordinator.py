from __future__ import annotations

import typing

from homeassistant.components.climate import HVACMode
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import SERVICE_TURN_ON, SERVICE_TURN_OFF, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant

from ..config_store import SatConfigStore
from ..const import *
from ..coordinator import DeviceState, SatDataUpdateCoordinator

if typing.TYPE_CHECKING:
    from ..climate import SatClimate


class SatSwitchCoordinator(SatDataUpdateCoordinator):
    """Class to manage the Switch."""

    def __init__(self, hass: HomeAssistant, store: SatConfigStore) -> None:
        """Initialize."""
        super().__init__(hass, store)
        self._entity_id = self._store.options.get(CONF_SWITCH)

    async def async_control_heating_loop(self, climate: SatClimate, _time=None) -> None:
        """Control the max relative mod of the heating system."""
        await super().async_control_heating_loop(climate)

        if climate.hvac_mode == HVACMode.OFF and self.hass.states.get(self._entity_id).state != "OFF":
            await self.async_set_heater_state(DeviceState.OFF)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        """Control the state of the central heating."""
        if not self._simulation:
            service = SERVICE_TURN_ON if state == DeviceState.ON else SERVICE_TURN_OFF
            await self.hass.services.async_call(SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: self._entity_id}, blocking=True)

        await super().async_set_heater_state(state)
