from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from aioesphomeapi import (
    APIClient,
    BinarySensorInfo,
    BinarySensorState,
    EntityInfo,
    NumberInfo,
    NumberState,
    SensorInfo,
    SensorState,
    SwitchInfo,
    SwitchState,
)
from homeassistant.core import HomeAssistant, callback, CALLBACK_TYPE
from homeassistant.exceptions import ConfigEntryNotReady

from .. import SatDataUpdateCoordinator
from ...entry_data import SatConfig
from ...types import HeaterState

if TYPE_CHECKING:
    from homeassistant.components.esphome.entry_data import RuntimeEntryData

_LOGGER: logging.Logger = logging.getLogger(__name__)

ENTITY_MAP: dict[tuple[type[EntityInfo], str], str] = {
    # Sensors (read-only)
    (SensorInfo, "t_boiler"): "t_boiler",
    (SensorInfo, "t_ret"): "t_ret",
    (SensorInfo, "rel_mod_level"): "rel_mod_level",
    (SensorInfo, "device_id"): "device_id",
    (SensorInfo, "max_capacity"): "max_capacity",
    (SensorInfo, "min_mod_level"): "min_mod_level",
    (SensorInfo, "t_dhw_set_lb"): "t_dhw_set_lb",
    (SensorInfo, "t_dhw_set_ub"): "t_dhw_set_ub",
    (SensorInfo, "ch_pressure"): "ch_pressure",

    # Binary sensors (read-only)
    (BinarySensorInfo, "flame_on"): "flame_on",
    (BinarySensorInfo, "dhw_active"): "dhw_active",
    (BinarySensorInfo, "ch_active"): "ch_active",

    # Numbers (read-write setpoints)
    (NumberInfo, "t_set"): "t_set",
    (NumberInfo, "t_dhw_set"): "t_dhw_set",
    (NumberInfo, "max_t_set"): "max_t_set",
    (NumberInfo, "max_rel_mod_level"): "max_rel_mod_level",

    # Switches (read-write binary controls)
    (SwitchInfo, "ch_enable"): "ch_enable",
    (SwitchInfo, "dhw_enable"): "dhw_enable",
}


@dataclass
class EspHomeEntityKeys:
    """Resolved ESPHome numeric keys for OpenTherm data points."""

    # Sensors
    t_boiler: Optional[int] = None
    t_ret: Optional[int] = None
    rel_mod_level: Optional[int] = None
    device_id: Optional[int] = None
    max_capacity: Optional[int] = None
    min_mod_level: Optional[int] = None
    t_dhw_set_lb: Optional[int] = None
    t_dhw_set_ub: Optional[int] = None
    ch_pressure: Optional[int] = None

    # Binary sensors
    flame_on: Optional[int] = None
    dhw_active: Optional[int] = None
    ch_active: Optional[int] = None

    # Numbers
    t_set: Optional[int] = None
    t_dhw_set: Optional[int] = None
    max_t_set: Optional[int] = None
    max_rel_mod_level: Optional[int] = None

    # Switches
    ch_enable: Optional[int] = None
    dhw_enable: Optional[int] = None


class SatEspHomeCoordinator(SatDataUpdateCoordinator):
    """Manage data from an ESPHome OpenTherm device via the native API."""

    def __init__(self, hass: HomeAssistant, config: SatConfig) -> None:
        super().__init__(hass, config)

        self._client: APIClient | None = None
        self._unsub_callbacks: list[CALLBACK_TYPE] = []
        self._entry_data: RuntimeEntryData | None = None
        self._keys: EspHomeEntityKeys = EspHomeEntityKeys()

        esphome_entry_id = config.device
        if esphome_entry_id is None:
            raise ConfigEntryNotReady("ESPHome config entry ID not configured")

        esphome_entry = hass.config_entries.async_get_entry(esphome_entry_id)
        if esphome_entry is None:
            raise ConfigEntryNotReady(f"ESPHome config entry {esphome_entry_id} not found")

        try:
            self._entry_data = esphome_entry.runtime_data
        except AttributeError:
            raise ConfigEntryNotReady("ESPHome integration not yet initialized")

        self._client = self._entry_data.client

    @property
    def id(self) -> str:
        if self._entry_data.device_info is not None:
            return self._entry_data.device_info.mac_address
        return self._config.device

    @property
    def type(self) -> str:
        return "ESPHome"

    @property
    def supports_setpoint_management(self):
        return self._keys.t_set is not None

    @property
    def supports_hot_water_setpoint_management(self):
        return self._keys.t_dhw_set is not None

    @property
    def supports_maximum_setpoint_management(self):
        return self._keys.max_t_set is not None

    @property
    def supports_relative_modulation(self):
        return self._keys.rel_mod_level is not None and super().supports_relative_modulation

    @property
    def active(self) -> bool:
        return self._get_switch_value(self._keys.ch_enable) or False

    @property
    def flame_active(self) -> bool:
        return self._get_binary_sensor_value(self._keys.flame_on) or False

    @property
    def hot_water_active(self) -> bool:
        return self._get_binary_sensor_value(self._keys.dhw_active) or False

    @property
    def setpoint(self) -> Optional[float]:
        return self._get_number_value(self._keys.t_set)

    @property
    def hot_water_setpoint(self) -> Optional[float]:
        return self._get_number_value(self._keys.t_dhw_set)

    @property
    def minimum_hot_water_setpoint(self) -> float:
        return self._get_sensor_value(self._keys.t_dhw_set_lb) or super().minimum_hot_water_setpoint

    @property
    def maximum_hot_water_setpoint(self) -> float:
        return self._get_sensor_value(self._keys.t_dhw_set_ub) or super().maximum_hot_water_setpoint

    @property
    def boiler_temperature(self) -> Optional[float]:
        return self._get_sensor_value(self._keys.t_boiler)

    @property
    def return_temperature(self) -> Optional[float]:
        return self._get_sensor_value(self._keys.t_ret)

    @property
    def boiler_pressure(self) -> Optional[float]:
        return self._get_sensor_value(self._keys.ch_pressure)

    @property
    def relative_modulation_value(self) -> Optional[float]:
        return self._get_sensor_value(self._keys.rel_mod_level)

    @property
    def boiler_capacity(self) -> Optional[float]:
        return self._get_sensor_value(self._keys.max_capacity)

    @property
    def minimum_relative_modulation_value(self) -> Optional[float]:
        return self._get_sensor_value(self._keys.min_mod_level)

    @property
    def maximum_relative_modulation_value(self) -> Optional[float]:
        return self._get_number_value(self._keys.max_rel_mod_level)

    @property
    def maximum_setpoint_value(self) -> Optional[float]:
        return self._get_number_value(self._keys.max_t_set)

    @property
    def member_id(self) -> Optional[int]:
        value = self._get_sensor_value(self._keys.device_id)
        return int(value) if value is not None else None

    async def async_added_to_hass(self, hass: HomeAssistant) -> None:
        # Discover entity keys from the ESPHome device info
        self._keys = self._discover_entities()
        _LOGGER.debug("Discovered ESPHome entity keys: %s", self._keys)

        # Subscribe to state updates for all discovered keys
        for state_type, key_fields in [
            (SensorState, [
                "t_boiler", "t_ret", "rel_mod_level", "device_id",
                "max_capacity", "min_mod_level", "t_dhw_set_lb",
                "t_dhw_set_ub", "ch_pressure"
            ]),

            (BinarySensorState, [
                "flame_on", "dhw_active", "ch_active"
            ]),

            (NumberState, [
                "t_set", "t_dhw_set", "max_t_set", "max_rel_mod_level"
            ]),

            (SwitchState, [
                "ch_enable", "dhw_enable"
            ]),
        ]:
            for field_name in key_fields:
                key = getattr(self._keys, field_name)
                if key is not None:
                    unsub = self._entry_data.async_subscribe_state_update(
                        device_id=0,
                        state_type=state_type,
                        state_key=key,
                        entity_callback=self._on_state_update,
                    )
                    self._unsub_callbacks.append(unsub)

        # Subscribe to device-level updates (availability changes, reconnects)
        unsub = self._entry_data.async_subscribe_device_updated(self._on_device_updated)
        self._unsub_callbacks.append(unsub)

        await super().async_added_to_hass(hass)

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._unsub_callbacks:
            unsub()

        self._unsub_callbacks.clear()
        await super().async_will_remove_from_hass()

    async def async_set_control_setpoint(self, value: float) -> None:
        if not self._config.simulation.enabled and self._keys.t_set is not None:
            self._client.number_command(self._keys.t_set, value)
            _LOGGER.debug("Sent control setpoint %.1f to ESPHome", value)

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        if not self._config.simulation.enabled and self._keys.t_dhw_set is not None:
            self._client.number_command(self._keys.t_dhw_set, value)
            _LOGGER.debug("Sent DHW setpoint %.1f to ESPHome", value)

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_heater_state(self, state: HeaterState) -> None:
        if not self._config.simulation.enabled and self._keys.ch_enable is not None:
            self._client.switch_command(self._keys.ch_enable, state == HeaterState.ON)
            _LOGGER.debug("Sent heater state %s to ESPHome", state)

        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        if not self._config.simulation.enabled and self._keys.max_rel_mod_level is not None:
            self._client.number_command(self._keys.max_rel_mod_level, float(value))
            _LOGGER.debug("Sent max relative modulation %d to ESPHome", value)

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        if not self._config.simulation.enabled and self._keys.max_t_set is not None:
            self._client.number_command(self._keys.max_t_set, value)
            _LOGGER.debug("Sent max setpoint %.1f to ESPHome", value)

        await super().async_set_control_max_setpoint(value)

    def _discover_entities(self) -> EspHomeEntityKeys:
        """Scan entry_data.info to find entity keys by object_id."""
        keys = EspHomeEntityKeys()

        for info_type in (SensorInfo, BinarySensorInfo, NumberInfo, SwitchInfo):
            infos = self._entry_data.info.get(info_type, {})
            for (_device_id, key), info in infos.items():
                field_name = ENTITY_MAP.get((info_type, info.object_id))
                if field_name is not None:
                    setattr(keys, field_name, key)

        return keys

    def _get_sensor_value(self, key: Optional[int]) -> Optional[float]:
        """Read a sensor value from the ESPHome state cache."""
        if key is None:
            return None

        state = self._entry_data.state[SensorState].get(key)
        if state is None or getattr(state, "missing_state", False):
            return None

        return state.state

    def _get_binary_sensor_value(self, key: Optional[int]) -> Optional[bool]:
        """Read a binary sensor value from the ESPHome state cache."""
        if key is None:
            return None

        state = self._entry_data.state[BinarySensorState].get(key)
        if state is None or getattr(state, "missing_state", False):
            return None

        return state.state

    def _get_number_value(self, key: Optional[int]) -> Optional[float]:
        """Read a number value from the ESPHome state cache."""
        if key is None:
            return None

        state = self._entry_data.state[NumberState].get(key)
        if state is None or getattr(state, "missing_state", False):
            return None

        return state.state

    def _get_switch_value(self, key: Optional[int]) -> Optional[bool]:
        """Read a switch value from the ESPHome state cache."""
        if key is None:
            return None

        state = self._entry_data.state[SwitchState].get(key)
        if state is None:
            return None

        return state.state

    @callback
    def _on_state_update(self) -> None:
        """Called when any subscribed entity state changes."""
        self.async_notify_listeners()

    @callback
    def _on_device_updated(self) -> None:
        """Called when device availability changes or reconnects."""
        # Re-discover entities in case firmware was updated
        new_keys = self._discover_entities()
        if new_keys != self._keys:
            _LOGGER.info("ESPHome entity keys changed after reconnection, re-discovering")
            self._keys = new_keys

        self.async_notify_listeners()
