import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyotgw import OpenThermGateway
from serial import SerialException

from .const import (
    CONF_ID,
    CONF_DEVICE,
    DOMAIN,
    SENSOR,
    CLIMATE,
    BINARY_SENSOR,
    CONF_UNDERFLOOR,
    CONF_RADIATOR_LOW_TEMPERATURES,
    CONF_RADIATOR_HIGH_TEMPERATURES, COORDINATOR,
)

SCAN_INTERVAL = timedelta(seconds=15)

_LOGGER: logging.Logger = logging.getLogger(__package__)


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

    coordinator = SatDataUpdateCoordinator(hass, client=client)
    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        raise ConfigEntryNotReady

    hass.data[DOMAIN][entry.entry_id] = {
        COORDINATOR: coordinator,
        CLIMATE: None
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

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)

    async def _async_update_data(self):
        """Update data via library."""
        try:
            return await self.api.get_status()
        except Exception as exception:
            raise UpdateFailed() from exception

    async def cleanup(self, event=None):
        """Reset overrides on the gateway."""
        await self.api.set_control_setpoint(0)
        await self.api.set_max_relative_mod("-")
        await self.api.disconnect()
