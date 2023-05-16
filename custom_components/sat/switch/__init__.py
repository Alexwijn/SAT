from __future__ import annotations

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import SERVICE_TURN_ON, SERVICE_TURN_OFF, ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import HomeAssistant

from ..config_store import SatConfigStore
from ..coordinator import DeviceState, SatDataUpdateCoordinator


class SatSwitchCoordinator(SatDataUpdateCoordinator):
    """Class to manage the Switch."""

    def __init__(self, hass: HomeAssistant, store: SatConfigStore, entity_id: str) -> None:
        """Initialize."""
        super().__init__(hass, store)

        self._entity_id = entity_id

    @property
    def setpoint(self) -> float:
        return self.minimum_setpoint

    @property
    def maximum_setpoint(self) -> float:
        return self.minimum_setpoint

    @property
    def device_active(self) -> bool:
        if (state := self.hass.states.get(self._entity_id)) is None:
            return False

        return state.state == STATE_ON

    async def async_set_heater_state(self, state: DeviceState) -> None:
        if not self._simulation:
            service = SERVICE_TURN_ON if state == DeviceState.ON else SERVICE_TURN_OFF
            await self.hass.services.async_call(SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: self._entity_id}, blocking=True)

        await super().async_set_heater_state(state)
