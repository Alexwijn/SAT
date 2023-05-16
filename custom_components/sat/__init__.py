import asyncio
import logging

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config, HomeAssistant

from . import mqtt, serial, switch
from .config_store import SatConfigStore
from .const import *
from .coordinator import SatDataUpdateCoordinatorFactory

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup(_hass: HomeAssistant, __config: Config):
    """
    Set up this integration using YAML is not supported.

    This function is not needed for this integration, but it is required by the Home Assistant framework.
    """
    return True


async def async_setup_entry(_hass: HomeAssistant, _entry: ConfigEntry):
    """
    Set up this integration using the UI.

    This function is called by Home Assistant when the integration is set up with the UI.
    """
    if _hass.data.get(DOMAIN) is None:
        _hass.data.setdefault(DOMAIN, {})

    # Create a new dictionary
    _hass.data[DOMAIN][_entry.entry_id] = {}

    # Create a new config store for this entry and initialize it
    _hass.data[DOMAIN][_entry.entry_id][CONFIG_STORE] = store = SatConfigStore(_hass, _entry)
    await _hass.data[DOMAIN][_entry.entry_id][CONFIG_STORE].async_initialize()

    # Resolve the coordinator by using the factory according to the mode
    _hass.data[DOMAIN][_entry.entry_id][COORDINATOR] = await SatDataUpdateCoordinatorFactory().resolve(
        hass=_hass, store=store, mode=store.options.get(CONF_MODE), device=store.options.get(CONF_DEVICE)
    )

    # Forward entry setup for climate and other platforms
    await _hass.async_add_job(_hass.config_entries.async_forward_entry_setup(_entry, CLIMATE_DOMAIN))
    await _hass.async_add_job(_hass.config_entries.async_forward_entry_setups(_entry, [SENSOR_DOMAIN, NUMBER_DOMAIN, BINARY_SENSOR_DOMAIN]))

    return True


async def async_unload_entry(_hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """
    Handle removal of an entry.

    This function is called by Home Assistant when the integration is being removed.
    """

    climate = _hass.data[DOMAIN][_entry.entry_id][CLIMATE]
    await _hass.data[DOMAIN][_entry.entry_id][COORDINATOR].async_will_remove_from_hass(climate)

    unloaded = all(
        await asyncio.gather(
            _hass.config_entries.async_unload_platforms(_entry, [CLIMATE_DOMAIN, SENSOR_DOMAIN, NUMBER_DOMAIN, BINARY_SENSOR_DOMAIN]),
        )
    )

    # Remove the entry from the data dictionary if all components are unloaded successfully
    if unloaded:
        _hass.data[DOMAIN].pop(_entry.entry_id)

    return unloaded


async def async_reload_entry(_hass: HomeAssistant, _entry: ConfigEntry) -> None:
    """
    Reload config entry.

    This function is called by Home Assistant when the integration configuration is updated.
    """
    # Unload the entry and its dependent components
    await async_unload_entry(_hass, _entry)

    # Set up the entry again
    await async_setup_entry(_hass, _entry)
