import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyotgw import OpenThermGateway
from serial import SerialException

from .const import (
    CONF_DEVICE,
    DOMAIN,
    SENSOR,
    CLIMATE,
    BINARY_SENSOR,
    CONF_UNDERFLOOR,
    CONF_RADIATOR_LOW_TEMPERATURES,
    CONF_RADIATOR_HIGH_TEMPERATURES, COORDINATOR, STORAGE_OVERSHOOT_PROTECTION_VALUE,
)

_LOGGER: logging.Logger = logging.getLogger(__name__)


def mean(values):
    if len(values) == 0:
        return 0

    return sum(values) / len(values)


async def async_setup(hass: HomeAssistant, config: Config):
    """Set up this integration using YAML is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""

    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})

    try:
        client = OpenThermGateway()
        await client.connect(port=entry.data.get(CONF_DEVICE), timeout=5)
    except (asyncio.TimeoutError, ConnectionError, SerialException) as ex:
        raise ConfigEntryNotReady(f"Could not connect to gateway at {entry.data.get(CONF_DEVICE)}: {ex}") from ex

    hass.data[DOMAIN][entry.entry_id] = {
        COORDINATOR: SatDataUpdateCoordinator(hass, client=client),
    }

    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, CLIMATE))
    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, SENSOR))
    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, BINARY_SENSOR))

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    unloaded = all(
        await asyncio.gather(
            hass.config_entries.async_forward_entry_unload(entry, CLIMATE),
            hass.config_entries.async_forward_entry_unload(entry, SENSOR),
            hass.config_entries.async_forward_entry_unload(entry, BINARY_SENSOR)
        )
    )

    if unloaded:
        coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
        await coordinator.cleanup()

        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


class SatDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the OTGW Gateway."""

    def __init__(self, hass: HomeAssistant, client: OpenThermGateway) -> None:
        """Initialize."""
        self.api = client
        self.api.subscribe(self._async_coroutine)

        super().__init__(hass, _LOGGER, name=DOMAIN)

    async def _async_update_data(self):
        """Update data via library."""
        try:
            return await self.api.get_status()
        except Exception as exception:
            raise UpdateFailed() from exception

    async def _async_coroutine(self, data):
        self.async_set_updated_data(data)

    async def cleanup(self):
        """Cleanup and disconnect."""
        self.api.unsubscribe(self._async_coroutine)

        await self.api.set_control_setpoint(0)
        await self.api.set_max_relative_mod("-")
        await self.api.disconnect()


class SatConfigStore:
    _STORAGE_VERSION = 1
    _STORAGE_KEY = DOMAIN

    def __init__(self, hass):
        self._hass = hass
        self._data = None
        self._store = Store(hass, self._STORAGE_VERSION, self._STORAGE_KEY)

    async def async_initialize(self):
        if (data := await self._store.async_load()) is None:
            data = {STORAGE_OVERSHOOT_PROTECTION_VALUE: None}

        self._data = data

    def retrieve_overshoot_protection_value(self):
        return self._data[STORAGE_OVERSHOOT_PROTECTION_VALUE]

    def store_overshoot_protection_value(self, value: float):
        self._data[STORAGE_OVERSHOOT_PROTECTION_VALUE] = value
        self._store.async_delay_save(lambda: self._data, 1.0)
