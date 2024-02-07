from __future__ import annotations, annotations

import logging
from typing import TYPE_CHECKING, Mapping, Any

from homeassistant.components import mqtt
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.mqtt import DOMAIN as MQTT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.event import async_track_state_change_event

from ..const import *
from ..coordinator import DeviceState, SatDataUpdateCoordinator

DATA_FLAME_ACTIVE = "flame"
DATA_DHW_SETPOINT = "TdhwSet"
DATA_CONTROL_SETPOINT = "TSet"
DATA_REL_MOD_LEVEL = "RelModLevel"
DATA_BOILER_TEMPERATURE = "Tboiler"
DATA_RETURN_TEMPERATURE = "Tret"
DATA_DHW_ENABLE = "domestichotwater"
DATA_CENTRAL_HEATING = "centralheating"
DATA_BOILER_CAPACITY = "MaxCapacityMinModLevel_hb_u8"
DATA_REL_MIN_MOD_LEVEL = "MaxCapacityMinModLevel_lb_u8"
DATA_REL_MIN_MOD_LEVELL = "MaxCapacityMinModLevell_lb_u8"
DATA_MAX_REL_MOD_LEVEL_SETTING = "MaxRelModLevelSetting"
DATA_DHW_SETPOINT_MINIMUM = "TdhwSetUBTdhwSetLB_value_lb"
DATA_DHW_SETPOINT_MAXIMUM = "TdhwSetUBTdhwSetLB_value_hb"

if TYPE_CHECKING:
    from ..climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatMqttCoordinator(SatDataUpdateCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using mqtt."""

    def __init__(self, hass: HomeAssistant, device_id: str, data: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> None:
        super().__init__(hass, data, options)

        self.data = {}

        self._device = device_registry.async_get(hass).async_get(device_id)
        self._node_id = list(self._device.identifiers)[0][1]
        self._topic = data.get(CONF_MQTT_TOPIC)

        self._entity_registry = entity_registry.async_get(hass)
        self._entities = entity_registry.async_entries_for_device(self._entity_registry, self._device.id)

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
        return self._get_entity_state(BINARY_SENSOR_DOMAIN, DATA_CENTRAL_HEATING) == DeviceState.ON

    @property
    def flame_active(self) -> bool:
        return self._get_entity_state(BINARY_SENSOR_DOMAIN, DATA_FLAME_ACTIVE) == DeviceState.ON

    @property
    def hot_water_active(self) -> bool:
        return self._get_entity_state(BINARY_SENSOR_DOMAIN, DATA_DHW_ENABLE) == DeviceState.ON

    @property
    def setpoint(self) -> float | None:
        if (setpoint := self._get_entity_state(SENSOR_DOMAIN, DATA_CONTROL_SETPOINT)) is not None:
            return float(setpoint)

        return None

    @property
    def hot_water_setpoint(self) -> float | None:
        if (setpoint := self._get_entity_state(SENSOR_DOMAIN, DATA_DHW_SETPOINT)) is not None:
            return float(setpoint)

        return super().hot_water_setpoint

    @property
    def minimum_hot_water_setpoint(self) -> float:
        if (setpoint := self._get_entity_state(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MINIMUM)) is not None:
            return float(setpoint)

        return super().minimum_hot_water_setpoint

    @property
    def maximum_hot_water_setpoint(self) -> float | None:
        if (setpoint := self._get_entity_state(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MAXIMUM)) is not None:
            return float(setpoint)

        return super().maximum_hot_water_setpoint

    @property
    def boiler_temperature(self) -> float | None:
        if (value := self._get_entity_state(SENSOR_DOMAIN, DATA_BOILER_TEMPERATURE)) is not None:
            return float(value)

        return super().boiler_temperature

    @property
    def return_temperature(self) -> float | None:
        if (value := self._get_entity_state(SENSOR_DOMAIN, DATA_RETURN_TEMPERATURE)) is not None:
            return float(value)

        return super().return_temperature

    @property
    def relative_modulation_value(self) -> float | None:
        if (value := self._get_entity_state(SENSOR_DOMAIN, DATA_REL_MOD_LEVEL)) is not None:
            return float(value)

        return super().relative_modulation_value

    @property
    def boiler_capacity(self) -> float | None:
        if (value := self._get_entity_state(SENSOR_DOMAIN, DATA_BOILER_CAPACITY)) is not None:
            return float(value)

        return super().boiler_capacity

    @property
    def minimum_relative_modulation_value(self) -> float | None:
        if (value := self._get_entity_state(SENSOR_DOMAIN, DATA_REL_MIN_MOD_LEVEL)) is not None:
            return float(value)

        # Legacy
        if (value := self._get_entity_state(SENSOR_DOMAIN, DATA_REL_MIN_MOD_LEVELL)) is not None:
            return float(value)

        return super().minimum_relative_modulation_value

    @property
    def maximum_relative_modulation_value(self) -> float | None:
        if (value := self._get_entity_state(SENSOR_DOMAIN, DATA_MAX_REL_MOD_LEVEL_SETTING)) is not None:
            return float(value)

        return super().maximum_relative_modulation_value

    async def async_added_to_hass(self, climate: SatClimate) -> None:
        await mqtt.async_wait_for_mqtt_client(self.hass)

        # Create a list of entities that we track
        entities = list(filter(lambda entity: entity is not None, [
            self._get_entity_id(BINARY_SENSOR_DOMAIN, DATA_CENTRAL_HEATING),
            self._get_entity_id(BINARY_SENSOR_DOMAIN, DATA_FLAME_ACTIVE),
            self._get_entity_id(BINARY_SENSOR_DOMAIN, DATA_DHW_ENABLE),

            self._get_entity_id(SENSOR_DOMAIN, DATA_DHW_SETPOINT),
            self._get_entity_id(SENSOR_DOMAIN, DATA_CONTROL_SETPOINT),
            self._get_entity_id(SENSOR_DOMAIN, DATA_REL_MOD_LEVEL),
            self._get_entity_id(SENSOR_DOMAIN, DATA_BOILER_TEMPERATURE),
            self._get_entity_id(SENSOR_DOMAIN, DATA_BOILER_CAPACITY),
            self._get_entity_id(SENSOR_DOMAIN, DATA_REL_MIN_MOD_LEVEL),
            self._get_entity_id(SENSOR_DOMAIN, DATA_REL_MIN_MOD_LEVELL),
            self._get_entity_id(SENSOR_DOMAIN, DATA_MAX_REL_MOD_LEVEL_SETTING),
            self._get_entity_id(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MINIMUM),
            self._get_entity_id(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MAXIMUM),
        ]))

        # Track those entities so the coordinator can be updated when something changes
        async_track_state_change_event(self.hass, entities, self.async_state_change_event)

        await self._send_command("PM=48")
        await super().async_added_to_hass(climate)

    async def async_state_change_event(self, event: Event):
        if self._listeners:
            self._schedule_refresh()

        self.async_update_listeners()

    async def async_set_control_setpoint(self, value: float) -> None:
        await self._send_command(f"CS={value}")

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        await self._send_command(f"SW={value}")

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        await self._send_command(f"TC={value}")

        await super().async_set_control_thermostat_setpoint(value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        await self._send_command(f"CH={1 if state == DeviceState.ON else 0}")

        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        await self._send_command(f"MM={value}")

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        await self._send_command(f"SH={value}")

        await super().async_set_control_max_setpoint(value)

    def _get_entity_state(self, domain: str, key: str):
        entity_id = self._get_entity_id(domain, key)
        if entity_id is None:
            return None

        state = self.hass.states.get(self._get_entity_id(domain, key))
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None

        return state.state

    def _get_entity_id(self, domain: str, key: str):
        return self._entity_registry.async_get_entity_id(domain, MQTT_DOMAIN, f"{self._node_id}-{key}")

    async def _send_command(self, payload: str):
        if not self._simulation:
            await mqtt.async_publish(self.hass, f"{self._topic}/set/{self._node_id}/command", payload)

        _LOGGER.debug(f"Publishing '{payload}' to MQTT.")
