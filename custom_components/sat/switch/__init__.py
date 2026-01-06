from __future__ import annotations

from typing import Optional

from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import SERVICE_TURN_ON, SERVICE_TURN_OFF, ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_registry import RegistryEntry

from ..coordinator import SatDataUpdateCoordinator
from ..entry_data import SatConfig
from ..types import DeviceState

DOMAIN_SERVICE = {
    SWITCH_DOMAIN: SWITCH_DOMAIN,
    INPUT_BOOLEAN_DOMAIN: INPUT_BOOLEAN_DOMAIN
}


class SatSwitchCoordinator(SatDataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config: SatConfig) -> None:
        """Initialize."""
        super().__init__(hass, config)

        self._entity: RegistryEntry = entity_registry.async_get(hass).async_get(self._config.device)

    @property
    def device_id(self) -> str:
        return self._entity.name

    @property
    def device_type(self) -> str:
        return "Switch"

    @property
    def setpoint(self) -> float:
        return self.minimum_setpoint

    @property
    def maximum_setpoint(self) -> float:
        return self.minimum_setpoint

    @property
    def device_active(self) -> bool:
        if (state := self.hass.states.get(self._entity.id)) is None:
            return False

        return state.state == STATE_ON

    @property
    def member_id(self) -> Optional[int]:
        return -1

    async def async_set_heater_state(self, state: DeviceState) -> None:
        if not self._config.simulation.enabled:
            domain_service = DOMAIN_SERVICE.get(self._entity.domain)
            state_service = SERVICE_TURN_ON if state == DeviceState.ON else SERVICE_TURN_OFF

            if domain_service:
                await self.hass.services.async_call(domain_service, state_service, {ATTR_ENTITY_ID: self._entity.entity_id}, blocking=True)

        await super().async_set_heater_state(state)
