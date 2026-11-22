"""The stores that belong to a config entry."""
from __future__ import annotations

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import CONF_DEVICE, DOMAIN
from .entry_data import SatConfig, SatMode

STORAGE_VERSION = 1
ENTRY_STORES = ("preheat", "room_balance", "wind_sensitivity", "boiler_health", "curve_advisor")
DEVICE_STORES = ("device", "boiler", "mqtt")


class EntryStores:
    """Find and remove what SAT stored for a config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._config = SatConfig(entry_id=entry.entry_id, data=entry.data, options=entry.options)

    async def async_remove(self) -> None:
        """Remove the stores of the entry, sparing those of a device another entry still uses."""
        keys = [f"sat.{name}.{self._entry.entry_id}" for name in ENTRY_STORES]

        for device_id in self._device_ids():
            keys.extend(f"sat.{name}.{device_id}" for name in DEVICE_STORES)
            keys.extend(f"sat.pid.{entity_id}.{device_id}" for entity_id in self._controlled_rooms())

        for key in keys:
            await Store(self._hass, STORAGE_VERSION, key).async_remove()

    def _controlled_rooms(self) -> list[str]:
        climate = er.async_get(self._hass).async_get_entity_id(CLIMATE_DOMAIN, DOMAIN, self._entry.entry_id)

        return [*self._config.rooms, *([climate] if climate is not None else [])]

    def _device_ids(self) -> set[str]:
        """Return the ids the coordinator of this entry can have stored under."""
        if (device := self._entry.data.get(CONF_DEVICE)) is None:
            return set()

        others = [entry for entry in self._hass.config_entries.async_entries(DOMAIN) if entry.entry_id != self._entry.entry_id]

        if any(entry.data.get(CONF_DEVICE) == device for entry in others):
            return set()

        device_ids = {str(device)}

        if self._config.mode == SatMode.SWITCH and (switch := er.async_get(self._hass).async_get(device)) is not None:
            device_ids.add(switch.id)

        if self._config.mode == SatMode.ESPHOME and (esphome := self._hass.config_entries.async_get_entry(device)) is not None:
            if esphome.unique_id is not None:
                device_ids.add(esphome.unique_id.upper())

        return device_ids
