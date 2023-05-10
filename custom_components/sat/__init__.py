import asyncio
import logging
import sys

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config, HomeAssistant

from . import mqtt, serial, switch
from .config_store import SatConfigStore
from .const import *

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup(_hass: HomeAssistant, __config: Config):
    """
    Set up this integration using YAML is not supported.

    This function is not needed for this integration, but it is required by the Home Assistant framework.
    """
    return True


async def async_setup_entry(_hass: HomeAssistant, _entry: ConfigEntry):
    """
    Set up this integration using UI.

    This function is called by Home Assistant when the integration is set up using the UI.
    """

    # Create a new dictionary for this entry if it doesn't exist
    if _hass.data.get(DOMAIN) is None:
        _hass.data.setdefault(DOMAIN, {})

    # Create a new config store for this entry and initialize it
    _hass.data[DOMAIN][_entry.entry_id] = {CONFIG_STORE: SatConfigStore(_hass, _entry)}
    await _hass.data[DOMAIN][_entry.entry_id][CONFIG_STORE].async_initialize()

    # Retrieve the defaults and override it with the user options
    options = OPTIONS_DEFAULTS.copy()
    options.update(_entry.data)

    # Get the module name from the config entry data and import it dynamically
    module = getattr(sys.modules[__name__], options.get(CONF_MODE))

    # Call the async_setup_entry function of the module
    await _hass.async_add_job(module.async_setup_entry, _hass, _entry)

    # Forward entry setup for climate and other platforms
    await _hass.async_add_job(_hass.config_entries.async_forward_entry_setup(_entry, CLIMATE))
    await _hass.async_add_job(_hass.config_entries.async_forward_entry_setups(_entry, [SENSOR, NUMBER, BINARY_SENSOR]))

    # Add an update listener for this entry
    _entry.async_on_unload(_entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(_hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """
    Handle removal of an entry.

    This function is called by Home Assistant when the integration is being removed.
    """

    # Retrieve the defaults and override it with the user options
    options = OPTIONS_DEFAULTS.copy()
    options.update(_entry.data)

    # Unload the entry and its dependent components
    unloaded = all(
        await asyncio.gather(
            _hass.config_entries.async_unload_platforms(_entry, [CLIMATE, SENSOR, NUMBER, BINARY_SENSOR]),
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
