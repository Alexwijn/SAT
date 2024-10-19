from __future__ import annotations, annotations

# Daikin Altherma 3 RF manual: 
# - English: https://www.daikin.be/content/dam/document-library/Installer-reference-guide/heat/air-to-water-heat-pump-low-temperature/EHFZ-D3V.ERLA-DV_Installer%20reference%20guide_4PEN596821-1B_English.pdf
# - Dutch: https://www.daikin.eu/content/dam/document-library/Installer-reference-guide/heat/air-to-water-heat-pump-low-temperature/EHFZ-D3V.ERLA-DV_Installer%20reference%20guide_4PNL596821-1B_Dutch.pdf

import logging
from typing import TYPE_CHECKING, Mapping, Any

from homeassistant.components import mqtt
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.mqtt import DOMAIN as MQTT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.event import async_track_state_change_event

from ..const import *
from ..coordinator import DeviceState, SatDataUpdateCoordinator, SatEntityCoordinator
from ..manufacturers.immergas import Immergas

DATA_FLAME_ACTIVE = "P1P2MQTT_bridge0_S1_Compressor_1"
DATA_DHW_SETPOINT = "P1P2MQTT_bridge0_S1_DHW_Setpoint_1"
DATA_CONTROL_SETPOINT = "P1P2MQTT_bridge0_S1_LWT_Setpoint_1"
DATA_REL_MOD_LEVEL = "TODO"
DATA_BOILER_TEMPERATURE = "P1P2MQTT_bridge0_T1_Temperature_R5T_DHW_Tank_1"
DATA_RETURN_TEMPERATURE = "P1P2MQTT_bridge0_T1_Temperature_R4T_Return_Water_1"
DATA_DHW_ENABLE = "P1P2MQTT_bridge0_S0_DHW_Setpoint_0" # mode: off, heat
DATA_CENTRAL_HEATING = "P1P2MQTT_bridge0_S0_Altherma_On_0"
DATA_BOILER_CAPACITY = "TODO"
DATA_REL_MIN_MOD_LEVEL = "P1P2MQTT_bridge0_C9_RT_Modulation_Max_9" # min
DATA_MAX_REL_MOD_LEVEL_SETTING = "P1P2MQTT_bridge0_C9_RT_Modulation_Max_9" # max
DATA_DHW_SETPOINT_MINIMUM = "P1P2MQTT_bridge0_S0_DHW_Setpoint_0" # min_temp
DATA_DHW_SETPOINT_MAXIMUM = "P1P2MQTT_bridge0_S0_DHW_Setpoint_0" # max_temp

if TYPE_CHECKING:
    from ..climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


class P1P2MqttCoordinator(SatDataUpdateCoordinator, SatEntityCoordinator):
    """Class to manage and fetch data from the P1P2MQTT bridge."""

    def __init__(self, hass: HomeAssistant, device_id: str, data: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> None:
        super().__init__(hass, data, options)

        self.data = {}

        # Device is P1P2
        self._device = device_registry.async_get(hass).async_get(device_id)
        # node_id is bridge0
        self._node_id = list(self._device.identifiers)[0][1]
        # Topic is P1P2/P/P1P2MQTT/bridge0
        self._topic = data.get(CONF_MQTT_TOPIC) 

        self._entity_registry = entity_registry.async_get(hass)
        self._entities = entity_registry.async_entries_for_device(self._entity_registry, self._device.id)

    @property
    def device_id(self) -> str:
        return self._node_id

    @property
    def supports_setpoint_management(self):
        return True

    @property
    def supports_hot_water_setpoint_management(self):
        return True

    def supports_maximum_setpoint_management(self):
        return False

    @property
    def supports_relative_modulation_management(self):
        return True

    @property
    def device_active(self) -> bool:
        return self.get(BINARY_SENSOR_DOMAIN, DATA_CENTRAL_HEATING) == DeviceState.ON

    @property
    def flame_active(self) -> bool:
        return self.get(BINARY_SENSOR_DOMAIN, DATA_FLAME_ACTIVE) == DeviceState.ON
        return False

    @property
    def hot_water_active(self) -> bool:
        return self.get(BINARY_SENSOR_DOMAIN, DATA_DHW_ENABLE) == DeviceState.ON

    @property
    def setpoint(self) -> float | None:
        if (setpoint := self.get(SENSOR_DOMAIN, DATA_CONTROL_SETPOINT)) is not None:
            return float(setpoint)

        return None

    @property
    def hot_water_setpoint(self) -> float | None:
        if (setpoint := self.get(SENSOR_DOMAIN, DATA_DHW_SETPOINT)) is not None:
            return float(setpoint)

        return super().hot_water_setpoint

    @property
    def minimum_hot_water_setpoint(self) -> float:
        # if (setpoint := self.get(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MINIMUM)) is not None:
        #     return float(setpoint)

        return super().minimum_hot_water_setpoint

    @property
    def maximum_hot_water_setpoint(self) -> float | None:
        # if (setpoint := self.get(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MAXIMUM)) is not None:
        #     return float(setpoint)

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
        # if (value := self.get(SENSOR_DOMAIN, DATA_REL_MOD_LEVEL)) is not None:
        #     return float(value)

        return super().relative_modulation_value

    @property
    def boiler_capacity(self) -> float | None:
        # if (value := self.get(SENSOR_DOMAIN, DATA_BOILER_CAPACITY)) is not None:
        #     return float(value)

        return super().boiler_capacity

    @property
    def minimum_relative_modulation_value(self) -> float | None:
        # if (value := self.get(SENSOR_DOMAIN, DATA_REL_MIN_MOD_LEVEL)) is not None:
        #     return float(value)

        return super().minimum_relative_modulation_value

    @property
    def maximum_relative_modulation_value(self) -> float | None:
        # if (value := self.get(SENSOR_DOMAIN, DATA_MAX_REL_MOD_LEVEL_SETTING)) is not None:
        #     return float(value)

        return super().maximum_relative_modulation_value

    @property
    def member_id(self) -> int | None:
        # Manufacturer not available on P1P2 bridge
        return None

    async def boot(self) -> SatMqttCoordinator:
        # TODO: p1p2 is always powered, no need to boot. So not needed?
        # We could set the p1p1 bridge to select RT for hc_fieldsettings_rt_lwt
        # and to use RT_modulation in hc_fieldsettings_rt_modulation
        # and set the RT_modulation_max to 10
        # await self._send_command("PM=3")
        # await self._send_command("PM=48")

        return self

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
            self._get_entity_id(SENSOR_DOMAIN, DATA_MAX_REL_MOD_LEVEL_SETTING),
            self._get_entity_id(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MINIMUM),
            self._get_entity_id(SENSOR_DOMAIN, DATA_DHW_SETPOINT_MAXIMUM),
        ]))

        # Track those entities so the coordinator can be updated when something changes
        async_track_state_change_event(self.hass, entities, self.async_state_change_event)

        await super().async_added_to_hass(climate)

    async def async_state_change_event(self, _event: Event):
        if self._listeners:
            self._schedule_refresh()

        self.async_update_listeners()

    async def async_set_control_setpoint(self, value: float) -> None:
        # TODO: Can be controlled with absolute value (Abs_Heating) or relative with weather dependent curve
        # What is the correct control to use?
        # await self._send_command(f"CS={value}")

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        # TODO: add DHW_Setpoint command
        # await self._send_command(f"SW={value}")

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        # TODO: Use Room_Heating, but requires p1p2 to be in RT mode
        # await self._send_command(f"TC={value}")

        await super().async_set_control_thermostat_setpoint(value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        # TODO: Is this the CH on/off switch? When will it be used?
        # Option to use: hc_mode_altherma_on
        # await self._send_command(f"CH={1 if state == DeviceState.ON else 0}")

        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        # TODO: unclear how to use this (same as sensor value)
        # await self._send_command(f"MM={value}")

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        # TODO: unclear what to use here. Set by user?
        # await self._send_command(f"SH={value}")

        await super().async_set_control_max_setpoint(value)

    def _get_entity_id(self, domain: str, key: str):
        return self._entity_registry.async_get_entity_id(domain, MQTT_DOMAIN, f"{key}")

    async def _send_command(self, payload: str):
        #if not self._simulation:
        #    await mqtt.async_publish(self.hass, f"{self._topic}/set/{self._node_id}/command", payload)

        _LOGGER.debug(f"Publishing '{payload}' to MQTT.")
