from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN, ValveState
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_CLOSE_VALVE, SERVICE_OPEN_VALVE, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_registry import RegistryEntry

from .. import SatDataUpdateCoordinator
from ...entry_data import SatConfig
from ...types import HeaterState


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaterEntityActions:
    active_state: str
    turn_on_service: str
    turn_off_service: str


SWITCH_ACTIONS = HeaterEntityActions(active_state=STATE_ON, turn_on_service=SERVICE_TURN_ON, turn_off_service=SERVICE_TURN_OFF)
VALVE_ACTIONS = HeaterEntityActions(active_state=ValveState.OPEN, turn_on_service=SERVICE_OPEN_VALVE, turn_off_service=SERVICE_CLOSE_VALVE)

DOMAIN_ACTIONS = {
    VALVE_DOMAIN: VALVE_ACTIONS,
    SWITCH_DOMAIN: SWITCH_ACTIONS,
    INPUT_BOOLEAN_DOMAIN: SWITCH_ACTIONS,
}


class SatSwitchCoordinator(SatDataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config: SatConfig) -> None:
        """Initialize."""
        super().__init__(hass, config)

        self._entity: RegistryEntry = entity_registry.async_get(hass).async_get(self._config.device)
        if self._entity is None:
            raise ConfigEntryNotReady(f"Switch entity {self._config.device} not found in the entity registry")

        self._actions: Optional[HeaterEntityActions] = DOMAIN_ACTIONS.get(self._entity.domain)

    @property
    def id(self) -> str:
        return self._entity.id

    @property
    def type(self) -> str:
        return "Switch"

    @property
    def setpoint(self) -> float:
        return self.minimum_setpoint

    @property
    def maximum_setpoint(self) -> float:
        return self.minimum_setpoint

    @property
    def active(self) -> bool:
        if self._actions is None or (state := self.hass.states.get(self._entity.entity_id)) is None:
            return False

        return state.state == self._actions.active_state

    @property
    def member_id(self) -> Optional[int]:
        return -1

    async def async_set_heater_state(self, state: HeaterState) -> None:
        if self._actions is not None and not self._config.simulation.enabled:
            service = self._actions.turn_on_service if state == HeaterState.ON else self._actions.turn_off_service

            await self.hass.services.async_call(self._entity.domain, service, {ATTR_ENTITY_ID: self._entity.entity_id}, blocking=True)

        await super().async_set_heater_state(state)
