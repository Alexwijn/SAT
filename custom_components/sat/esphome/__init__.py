from __future__ import annotations, annotations

import logging
from typing import TYPE_CHECKING, Mapping, Any

from homeassistant.components import mqtt
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.esphome import DOMAIN as ESPHOME_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.number.const import SERVICE_SET_VALUE
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN, SERVICE_TURN_ON, SERVICE_TURN_OFF
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.event import async_track_state_change_event

from ..coordinator import DeviceState, SatDataUpdateCoordinator, SatEntityCoordinator

# Sensors
DATA_FLAME_ACTIVE = "flame_active"
DATA_REL_MOD_LEVEL = "modulation"
DATA_SLAVE_MEMBERID = "boiler_member_id"
DATA_BOILER_TEMPERATURE = "boiler_temperature"
DATA_RETURN_TEMPERATURE = "return_temperature"
DATA_BOILER_CAPACITY = "max_capacity"
DATA_REL_MIN_MOD_LEVEL = "min_mod_level"

DATA_DHW_SETPOINT_MINIMUM = "dhw_min_temperature"
DATA_DHW_SETPOINT_MAXIMUM = "dhw_max_temperature"

# Switch
DATA_DHW_ENABLE = "dhw_enabled"
DATA_CENTRAL_HEATING = "ch_enabled"

# Number
DATA_DHW_SETPOINT = "dhw_setpoint_temperature"
DATA_CONTROL_SETPOINT = "ch_setpoint_temperature"
DATA_MAX_CH_SETPOINT = "max_ch_setpoint_temperature"
DATA_MAX_REL_MOD_LEVEL_SETTING = "max_modulation_level"

if TYPE_CHECKING:
    from ..climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatEspHomeCoordinator(SatDataUpdateCoordinator, SatEntityCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using esphome."""

    def __init__(self, hass: HomeAssistant, device_id: str, data: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> None:
        super().__init__(hass, data, options)

        self.data = {}

        self._device = device_registry.async_get(hass).async_get(device_id)
        self._mac_address = list(self._device.connections)[0][1]

        self._entity_registry = entity_registry.async_get(hass)
        self._entities = entity_registry.async_entries_for_device(self._entity_registry, self._device.id)

    @property
    def device_id(self) -> str:
        return self._mac_address

    @property
    def supports_setpoint_management(self):
        return True

    @property
    def supports_hot_water_setpoint_management(self):
        return True

    def supports_maximum_setpoint_management(self):
        return True

    @property
    def supports_relative_modulation_management(self):
        return True

    @property
    def device_active(self) -> bool:
        return self.get(SWITCH_DOMAIN, DATA_CENTRAL_HEATING) == DeviceState.ON

    @property
    def flame_active(self) -> bool:
        return self.get(BINARY_SENSOR_DOMAIN, DATA_FLAME_ACTIVE) == DeviceState.ON

    @property
    def hot_water_active(self) -> bool:
        return self.get(BINARY_SENSOR_DOMAIN, DATA_DHW_ENABLE) == DeviceState.ON

    @property
    def setpoint(self) -> float | None:
        if (setpoint := self.get(NUMBER_DOMAIN, DATA_CONTROL_SETPOINT)) is not None:
            return float(setpoint)

        return None

    @property
    def hot_water_setpoint(self) -> float | None:
        if (setpoint := self.get(NUMBER_DOMAIN, DATA_DHW_SETPOINT)) is not None:
            return float(setpoint)

        return super().hot_water_setpoint

    @property
    def minimum_hot_water_setpoint(self) -> float:
        if (setpoint := self.get(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MINIMUM)) is not None:
            return float(setpoint)

        return super().minimum_hot_water_setpoint

    @property
    def maximum_hot_water_setpoint(self) -> float | None:
        if (setpoint := self.get(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MAXIMUM)) is not None:
            return float(setpoint)

        return super().maximum_hot_water_setpoint

    @property
    def boiler_temperature(self) -> float | None:
        if (value := self.get(SENSOR_DOMAIN, DATA_BOILER_TEMPERATURE)) is not None:
            return float(value)

        return super().boiler_temperature

    @property
    def return_temperature(self) -> float | None:
        if (value := self.get(SENSOR_DOMAIN, DATA_RETURN_TEMPERATURE)) is not None:
            return float(value)

        return super().return_temperature

    @property
    def relative_modulation_value(self) -> float | None:
        if (value := self.get(SENSOR_DOMAIN, DATA_REL_MOD_LEVEL)) is not None:
            return float(value)

        return super().relative_modulation_value
    
    @property
    def boiler_capacity(self) -> float | None:
        if (value := self.get(SENSOR_DOMAIN, DATA_BOILER_CAPACITY)) is not None:
            return float(value)

        return super().boiler_capacity
    
    @property
    def minimum_relative_modulation_value(self) -> float | None:
        if (value := self.get(SENSOR_DOMAIN, DATA_REL_MIN_MOD_LEVEL)) is not None:
            return float(value)
        
        return super().minimum_relative_modulation_value

    @property
    def maximum_relative_modulation_value(self) -> float | None:
        if (value := self.get(NUMBER_DOMAIN, DATA_MAX_REL_MOD_LEVEL_SETTING)) is not None:
            return float(value)

        return super().maximum_relative_modulation_value

    @property
    def member_id(self) -> int | None:
        if (value := self.get(SENSOR_DOMAIN, DATA_SLAVE_MEMBERID)) is not None:
            return int(value)

        return None

    async def async_added_to_hass(self, climate: SatClimate) -> None:
        await mqtt.async_wait_for_mqtt_client(self.hass)

        # Create a list of entities that we track
        entities = list(filter(lambda entity: entity is not None, [
            self._get_entity_id(SENSOR_DOMAIN, DATA_FLAME_ACTIVE),
            self._get_entity_id(SENSOR_DOMAIN, DATA_REL_MOD_LEVEL),
            self._get_entity_id(SENSOR_DOMAIN, DATA_SLAVE_MEMBERID),
            self._get_entity_id(SENSOR_DOMAIN, DATA_BOILER_TEMPERATURE),
            self._get_entity_id(SENSOR_DOMAIN, DATA_RETURN_TEMPERATURE),

            self._get_entity_id(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MINIMUM),
            self._get_entity_id(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MAXIMUM),

            self._get_entity_id(SWITCH_DOMAIN, DATA_DHW_ENABLE),
            self._get_entity_id(SWITCH_DOMAIN, DATA_CENTRAL_HEATING),

            self._get_entity_id(NUMBER_DOMAIN, DATA_DHW_SETPOINT),
            self._get_entity_id(NUMBER_DOMAIN, DATA_CONTROL_SETPOINT),
            self._get_entity_id(NUMBER_DOMAIN, DATA_MAX_REL_MOD_LEVEL_SETTING),
        ]))

        # Track those entities so the coordinator can be updated when something changes
        async_track_state_change_event(self.hass, entities, self.async_state_change_event)

        await super().async_added_to_hass(climate)

    async def async_state_change_event(self, _event: Event):
        if self._listeners:
            self._schedule_refresh()

        self.async_update_listeners()

    async def async_set_control_setpoint(self, value: float) -> None:
        await self._send_command_value(DATA_CONTROL_SETPOINT, value)

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        await self._send_command_value(DATA_DHW_SETPOINT, value)

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        await self._send_command_state(DATA_CENTRAL_HEATING, state == DeviceState.ON)

        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        await self._send_command_value(DATA_MAX_REL_MOD_LEVEL_SETTING, value)

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        await self._send_command_value(DATA_MAX_CH_SETPOINT, value)

        await super().async_set_control_max_setpoint(value)

    def _get_entity_id(self, domain: str, key: str):
        unique_id = f"{self._mac_address.upper()}-{domain}-{key}"
        _LOGGER.debug(f"Attempting to find the unique_id of {unique_id}")
        return self._entity_registry.async_get_entity_id(domain, ESPHOME_DOMAIN, unique_id)

    async def _send_command(self, domain: str, service: str, key: str, payload: dict):
        """Helper method to send a command to a specified domain and service."""
        if not self._simulation:
            await self.hass.services.async_call(domain, service, payload, blocking=True)

        _LOGGER.debug(f"Sending '{payload}' to {service} in {domain}.")

    async def _send_command_state(self, key: str, value: bool):
        """Send a command to turn a switch on or off."""
        service = SERVICE_TURN_ON if value else SERVICE_TURN_OFF
        payload = {"entity_id": self._get_entity_id(SWITCH_DOMAIN, key)}
        await self._send_command(SWITCH_DOMAIN, service, key, payload)

    async def _send_command_value(self, key: str, value: float):
        """Send a command to set a numerical value."""
        payload = {"entity_id": self._get_entity_id(NUMBER_DOMAIN, key), "value": value}
        await self._send_command(NUMBER_DOMAIN, SERVICE_SET_VALUE, key, payload)
