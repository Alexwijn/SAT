from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import *


class SatConfigStore:
    _STORAGE_VERSION = 1
    _STORAGE_KEY = DOMAIN

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        self._data = {}
        self._hass = hass
        self._options = None
        self._config_entry = config_entry
        self._store = Store(hass, self._STORAGE_VERSION, self._STORAGE_KEY)

    async def async_initialize(self):
        if (data := await self._store.async_load()) is None:
            data = {STORAGE_OVERSHOOT_PROTECTION_VALUE: None}

        self._data = data
        self._options = OPTIONS_DEFAULTS.copy()
        self._options.update(self._config_entry.data)
        self._options.update(self._config_entry.options)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def update(self, key: str, value: float):
        self._data[key] = value
        self._store.async_delay_save(lambda: self._data, 1.0)

    @property
    def options(self):
        return self._options
