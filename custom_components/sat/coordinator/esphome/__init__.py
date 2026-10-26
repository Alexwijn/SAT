from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, TYPE_CHECKING

from aioesphomeapi import (
    APIClient,
    APIConnectionError,
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
from homeassistant.helpers.event import async_track_time_interval

from .. import SatDataUpdateCoordinator
from ...entry_data import SatConfig
from ...types import HeaterState

if TYPE_CHECKING:
    from homeassistant.components.esphome.entry_data import RuntimeEntryData

_LOGGER: logging.Logger = logging.getLogger(__name__)

STATE_NOTIFY_INTERVAL = timedelta(seconds=5)

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
    (BinarySensorInfo, "fault_indication"): "fault_indication",
    (SensorInfo, "oem_fault_code"): "oem_fault_code",

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
    fault_indication: Optional[int] = None
    oem_fault_code: Optional[int] = None

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

        self._keys: EspHomeEntityKeys = EspHomeEntityKeys()
        self._unsub_interval: CALLBACK_TYPE | None = None
        self._unsub_callbacks: list[CALLBACK_TYPE] = []
        self._subscribed_entry_data: RuntimeEntryData | None = None

        if config.device is None:
            raise ConfigEntryNotReady("ESPHome config entry ID not configured")

        if hass.config_entries.async_get_entry(config.device) is None:
            raise ConfigEntryNotReady(f"ESPHome config entry {config.device} not found")

        if self._entry_data is None:
            raise ConfigEntryNotReady("ESPHome integration not yet initialized")

    @property
    def _entry_data(self) -> RuntimeEntryData | None:
        """Return the live runtime data of the ESPHome entry; Home Assistant replaces it whenever that entry reloads."""
        if (entry := self.hass.config_entries.async_get_entry(self._config.device)) is None:
            return None

        return getattr(entry, "runtime_data", None)

    @property
    def _client(self) -> APIClient | None:
        if (entry_data := self._entry_data) is None:
            return None

        return entry_data.client

    @property
    def id(self) -> str:
        if (entry_data := self._entry_data) is not None and entry_data.device_info is not None:
            return entry_data.device_info.mac_address

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
    def supports_hot_water_management(self) -> bool:
        return self._keys.dhw_enable is not None

    @property
    def hot_water_enabled(self) -> Optional[bool]:
        return self._get_switch_value(self._keys.dhw_enable)

    @property
    def fault_active(self) -> Optional[bool]:
        return self._get_binary_sensor_value(self._keys.fault_indication)

    @property
    def fault_code(self) -> Optional[int]:
        if (code := self._get_sensor_value(self._keys.oem_fault_code)) is None:
            return None

        return int(code)

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
        self._keys = self._discover_entities()
        _LOGGER.debug("Discovered ESPHome entity keys: %s", self._keys)

        if self._unsub_interval is None:
            self._unsub_interval = async_track_time_interval(hass, self._async_notify_interval, STATE_NOTIFY_INTERVAL, cancel_on_shutdown=True)

        if (entry_data := self._entry_data) is not None:
            self._subscribe_to_device_updates(entry_data)

        await super().async_added_to_hass(hass)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None

        self._unsubscribe_from_device_updates()
        await super().async_will_remove_from_hass()

    async def async_set_control_setpoint(self, value: float) -> None:
        self._send_number_command(self._keys.t_set, value, "control setpoint")

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        self._send_number_command(self._keys.t_dhw_set, value, "DHW setpoint")

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_heater_state(self, state: HeaterState) -> None:
        self._send_switch_command(self._keys.ch_enable, state == HeaterState.ON, "heater state")

        await super().async_set_heater_state(state)

    async def async_set_hot_water_enabled(self, enabled: bool) -> None:
        self._send_switch_command(self._keys.dhw_enable, enabled, "hot water enabled")

        await super().async_set_hot_water_enabled(enabled)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        self._send_number_command(self._keys.max_rel_mod_level, float(value), "max relative modulation")

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        self._send_number_command(self._keys.max_t_set, value, "max setpoint")

        await super().async_set_control_max_setpoint(value)

    def _send_number_command(self, key: Optional[int], value: float, description: str) -> None:
        if self._config.simulation.enabled or key is None or (client := self._client) is None:
            return

        try:
            client.number_command(key, value)
        except APIConnectionError as error:
            _LOGGER.warning("Could not send %s to ESPHome: %s", description, error)
            return

        _LOGGER.debug("Sent %s %.1f to ESPHome", description, value)

    def _send_switch_command(self, key: Optional[int], state: bool, description: str) -> None:
        if self._config.simulation.enabled or key is None or (client := self._client) is None:
            return

        try:
            client.switch_command(key, state)
        except APIConnectionError as error:
            _LOGGER.warning("Could not send %s to ESPHome: %s", description, error)
            return

        _LOGGER.debug("Sent %s %s to ESPHome", description, state)

    def _subscribe_to_device_updates(self, entry_data: RuntimeEntryData) -> None:
        self._unsubscribe_from_device_updates()

        self._unsub_callbacks.append(entry_data.async_subscribe_static_info_updated(self._on_static_info_updated))
        self._unsub_callbacks.append(entry_data.async_subscribe_device_updated(self._on_device_updated))
        self._subscribed_entry_data = entry_data

    def _unsubscribe_from_device_updates(self) -> None:
        callbacks, self._unsub_callbacks = self._unsub_callbacks, []

        for unsub in callbacks:
            try:
                unsub()
            except KeyError:
                continue

        self._subscribed_entry_data = None

    def _discover_entities(self) -> EspHomeEntityKeys:
        """Scan entry_data.info to find entity keys by object_id."""
        keys = EspHomeEntityKeys()

        if (entry_data := self._entry_data) is None:
            return keys

        for info_type in (SensorInfo, BinarySensorInfo, NumberInfo, SwitchInfo):
            infos = entry_data.info.get(info_type, {})
            for (_device_id, key), info in infos.items():
                field_name = ENTITY_MAP.get((info_type, info.object_id))
                if field_name is not None:
                    setattr(keys, field_name, key)

        return keys

    def _get_sensor_value(self, key: Optional[int]) -> Optional[float]:
        """Read a sensor value from the ESPHome state cache."""
        if key is None or (entry_data := self._entry_data) is None:
            return None

        state = entry_data.state[SensorState].get(key)
        if state is None or getattr(state, "missing_state", False):
            return None

        return state.state

    def _get_binary_sensor_value(self, key: Optional[int]) -> Optional[bool]:
        """Read a binary sensor value from the ESPHome state cache."""
        if key is None or (entry_data := self._entry_data) is None:
            return None

        state = entry_data.state[BinarySensorState].get(key)
        if state is None or getattr(state, "missing_state", False):
            return None

        return state.state

    def _get_number_value(self, key: Optional[int]) -> Optional[float]:
        """Read a number value from the ESPHome state cache."""
        if key is None or (entry_data := self._entry_data) is None:
            return None

        state = entry_data.state[NumberState].get(key)
        if state is None or getattr(state, "missing_state", False):
            return None

        return state.state

    def _get_switch_value(self, key: Optional[int]) -> Optional[bool]:
        """Read a switch value from the ESPHome state cache."""
        if key is None or (entry_data := self._entry_data) is None:
            return None

        state = entry_data.state[SwitchState].get(key)
        if state is None:
            return None

        return state.state

    @callback
    def _async_notify_interval(self, _now=None) -> None:
        """Periodically notify listeners with the latest state cache values."""
        if (entry_data := self._entry_data) is not None and entry_data is not self._subscribed_entry_data:
            self._subscribe_to_device_updates(entry_data)
            self._refresh_keys()

        self.async_notify_listeners()

    def _refresh_keys(self) -> None:
        """Re-discover entity keys in case the firmware was updated."""
        new_keys = self._discover_entities()
        if new_keys != self._keys:
            _LOGGER.info("ESPHome entity keys changed, re-discovering")
            self._keys = new_keys

    @callback
    def _on_static_info_updated(self, _infos: list[EntityInfo]) -> None:
        """Called when the device sends new static entity info."""
        self._refresh_keys()
        self.async_notify_listeners()

    @callback
    def _on_device_updated(self) -> None:
        """Called when device availability changes or reconnects."""
        self._refresh_keys()
        self.async_notify_listeners()
