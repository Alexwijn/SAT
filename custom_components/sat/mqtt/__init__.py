from __future__ import annotations, annotations

import logging
import typing

from homeassistant.components import mqtt
from homeassistant.components.mqtt import DOMAIN as MQTT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, Event, State
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.event import async_track_state_change_event

from ..config_store import SatConfigStore
from ..const import *
from ..coordinator import DeviceState, SatDataUpdateCoordinator

DATA_FLAME_ACTIVE = "flame"
DATA_DHW_SETPOINT = "TdhwSet"
DATA_CONTROL_SETPOINT = "TSet"
DATA_DHW_ENABLE = "dhw_enable"
DATA_REL_MOD_LEVEL = "RelModLevel"
DATA_BOILER_TEMPERATURE = "Tboiler"
DATA_CENTRAL_HEATING = "centralheating"
DATA_BOILER_CAPACITY = "MaxCapacityMinModLevell_hb_u8"
DATA_REL_MIN_MOD_LEVEL = "MaxCapacityMinModLevell_lb_u8"
DATA_DHW_SETPOINT_MINIMUM = "TdhwSetUBTdhwSetLB_value_lb"
DATA_DHW_SETPOINT_MAXIMUM = "TdhwSetUBTdhwSetLB_value_hb"

if typing.TYPE_CHECKING:
    from ..climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


def entity_id_to_opentherm_key(hass: HomeAssistant, node_id: str, entity_id: str):
    entities = entity_registry.async_get(hass)
    entity = entities.async_get(entity_id)

    if entity.unique_id:
        return entity.unique_id[len(node_id) + 1:]

    return None


class SatMqttCoordinator(SatDataUpdateCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using mqtt."""

    def __init__(self, hass: HomeAssistant, store: SatConfigStore, device_id: str) -> None:
        super().__init__(hass, store)

        self.data = {}

        self._device = device_registry.async_get(hass).async_get(device_id)
        self._node_id = list(self._device.identifiers)[0][1]
        self._topic = store.options.get(CONF_MQTT_TOPIC)

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
    def support_relative_modulation_management(self):
        return True

    @property
    def device_active(self) -> bool:
        return bool(self.data.get(DATA_CENTRAL_HEATING))

    @property
    def flame_active(self) -> bool:
        return bool(self.data.get(DATA_FLAME_ACTIVE))

    @property
    def hot_water_active(self) -> bool:
        return bool(self.data.get(DATA_DHW_ENABLE))

    @property
    def setpoint(self) -> float | None:
        if (setpoint := self.data.get(DATA_CONTROL_SETPOINT)) is not None:
            return float(setpoint)

        return None

    @property
    def hot_water_setpoint(self) -> float | None:
        if (setpoint := self.data.get(DATA_DHW_SETPOINT)) is not None:
            return float(setpoint)

        return None

    @property
    def minimum_hot_water_setpoint(self) -> float:
        if (setpoint := self.data.get(DATA_DHW_SETPOINT_MINIMUM)) is not None:
            return float(setpoint)

        return super().minimum_hot_water_setpoint

    @property
    def maximum_hot_water_setpoint(self) -> float | None:
        if (setpoint := self.data.get(DATA_DHW_SETPOINT_MAXIMUM)) is not None:
            return float(setpoint)

        return super().maximum_hot_water_setpoint

    @property
    def boiler_temperature(self) -> float | None:
        if (value := self.data.get(DATA_BOILER_TEMPERATURE)) is not None:
            return float(value)

        return super().boiler_temperature

    @property
    def relative_modulation_value(self) -> float | None:
        if (value := self.data.get(DATA_REL_MOD_LEVEL)) is not None:
            return float(value)

        return None

    @property
    def boiler_capacity(self) -> float | None:
        if (value := self.data.get(DATA_BOILER_CAPACITY)) is not None:
            return float(value)

        return super().boiler_capacity

    @property
    def minimum_relative_modulation_value(self) -> float | None:
        if (value := self.data.get(DATA_REL_MIN_MOD_LEVEL)) is not None:
            return float(value)

        return super().boiler_capacity

    @property
    def minimum_setpoint(self) -> float:
        if (value := self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE)) is not None:
            return float(value)

        return super().minimum_setpoint

    async def async_added_to_hass(self, climate: SatClimate) -> None:
        await mqtt.async_wait_for_mqtt_client(self.hass)

        await self._send_command("PM=48")

        entities = list(filter(lambda entity: entity is not None, [
            self._get_entity_id(DATA_FLAME_ACTIVE),
            self._get_entity_id(DATA_DHW_SETPOINT),
            self._get_entity_id(DATA_CONTROL_SETPOINT),
            self._get_entity_id(DATA_DHW_ENABLE),
            self._get_entity_id(DATA_REL_MOD_LEVEL),
            self._get_entity_id(DATA_BOILER_TEMPERATURE),
            self._get_entity_id(DATA_CENTRAL_HEATING),
            self._get_entity_id(DATA_BOILER_CAPACITY),
            self._get_entity_id(DATA_REL_MIN_MOD_LEVEL),
            self._get_entity_id(DATA_DHW_SETPOINT_MINIMUM),
            self._get_entity_id(DATA_DHW_SETPOINT_MAXIMUM),
        ]))

        for entity_id in entities:
            if state := self.hass.states.get(entity_id):
                await self._on_state_change(entity_id, state)

        async def async_coroutine(event: Event):
            await self._on_state_change(event.data.get("entity_id"), event.data.get("new_state"))

        async_track_state_change_event(self.hass, entities, async_coroutine)

        await super().async_added_to_hass(climate)

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

    async def async_set_control_max_relative_modulation(self, value: float) -> None:
        await self._send_command(f"MM={value}")

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        await self._send_command(f"SH={value}")

        await super().async_set_control_max_setpoint(value)

    def _get_entity_id(self, key: str):
        return self._entity_registry.async_get_entity_id(SENSOR_DOMAIN, MQTT_DOMAIN, f"{self._node_id}-{key}")

    async def _on_state_change(self, entity_id: str, state: State):
        key = entity_id_to_opentherm_key(self.hass, self._node_id, entity_id)
        if key is None:
            return

        if state.state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self.data[key] = None
        else:
            self.data[key] = state.state

        if self._listeners:
            self._schedule_refresh()

        self.async_update_listeners()

    async def _send_command(self, payload: str):
        if not self._simulation:
            await mqtt.async_publish(self.hass, f"{self._topic}/set/{self._node_id}/command", payload)

        _LOGGER.debug(f"Publishing '{payload}' to MQTT.")
