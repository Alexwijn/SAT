import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config, HomeAssistant

from . import opentherm, switch
from .config_store import SatConfigStore
from .const import *

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup(_hass: HomeAssistant, __config: Config):
    """Set up this integration using YAML is not supported."""
    return True


async def async_setup_entry(_hass: HomeAssistant, _entry: ConfigEntry):
    """Set up this integration using UI."""
    if _hass.data.get(DOMAIN) is None:
        _hass.data.setdefault(DOMAIN, {})

    _hass.data[DOMAIN][_entry.entry_id] = {CONFIG_STORE: SatConfigStore(_hass, _entry)}
    await _hass.data[DOMAIN][_entry.entry_id][CONFIG_STORE].async_initialize()

    if _entry.data.get(CONF_MODE) == MODE_SWITCH:
        await _hass.async_add_job(switch.async_setup_entry(_hass, _entry))

    if _entry.data.get(CONF_MODE) == MODE_OPENTHERM:
        await _hass.async_add_job(opentherm.async_setup_entry(_hass, _entry))

    await _hass.async_add_job(_hass.config_entries.async_forward_entry_setups(_entry, [
        CLIMATE, SENSOR, NUMBER, BINARY_SENSOR
    ]))

    _entry.async_on_unload(_entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(_hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    unloaded = all(
        await asyncio.gather(
            _hass.config_entries.async_forward_entry_unload(_entry, _entry.data.get(CONF_MODE)),
            _hass.config_entries.async_forward_entry_unload(_entry, CLIMATE),
        )
    )

    if unloaded:
        _hass.data[DOMAIN].pop(_entry.entry_id)

    return unloaded


async def async_reload_entry(_hass: HomeAssistant, _entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(_hass, _entry)
    await async_setup_entry(_hass, _entry)
