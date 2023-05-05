import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from pyotgw import OpenThermGateway
from serial import SerialException

from .const import *
from .store import SatConfigStore

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup(_hass: HomeAssistant, __config: Config):
    """Set up this integration using YAML is not supported."""
    return True


async def async_setup_entry(_hass: HomeAssistant, _entry: ConfigEntry):
    """Set up this integration using UI."""
    if _hass.data.get(DOMAIN) is None:
        _hass.data.setdefault(DOMAIN, {})

    store = SatConfigStore(_hass, _entry)
    await store.async_initialize()

    if _entry.data.get(CONF_MODE) == MODE_SWITCH:
        from custom_components.sat.coordinators.switch import SatSwitchCoordinator
        _hass.data[DOMAIN][_entry.entry_id] = {COORDINATOR: SatSwitchCoordinator(_hass, store)}

    if _entry.data.get(CONF_MODE) == MODE_OPENTHERM:
        try:
            client = OpenThermGateway()
            await client.connect(port=_entry.data.get(CONF_DEVICE), timeout=5)
        except (asyncio.TimeoutError, ConnectionError, SerialException) as ex:
            raise ConfigEntryNotReady(f"Could not connect to gateway at {_entry.data.get(CONF_DEVICE)}: {ex}") from ex

        from custom_components.sat.coordinators.opentherm import SatOpenThermCoordinator
        _hass.data[DOMAIN][_entry.entry_id] = {COORDINATOR: SatOpenThermCoordinator(_hass, store, client)}

        await _hass.async_add_job(_hass.config_entries.async_forward_entry_setup(_entry, SENSOR))
        await _hass.async_add_job(_hass.config_entries.async_forward_entry_setup(_entry, NUMBER))
        await _hass.async_add_job(_hass.config_entries.async_forward_entry_setup(_entry, BINARY_SENSOR))

    _LOGGER.debug(_hass.data[DOMAIN][_entry.entry_id])
    await _hass.async_add_job(_hass.config_entries.async_forward_entry_setup(_entry, CLIMATE))

    _entry.async_on_unload(_entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(_hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    unloaded = True

    if _entry.data.get(CONF_MODE) == MODE_OPENTHERM:
        unloaded = all(
            await asyncio.gather(
                _hass.config_entries.async_forward_entry_unload(_entry, CLIMATE),
                _hass.config_entries.async_forward_entry_unload(_entry, SENSOR),
                _hass.config_entries.async_forward_entry_unload(_entry, NUMBER),
                _hass.config_entries.async_forward_entry_unload(_entry, BINARY_SENSOR),
                _hass.data[DOMAIN][_entry.entry_id][COORDINATOR].cleanup()
            )
        )

    if _entry.data.get(CONF_MODE) == MODE_SWITCH:
        unloaded = all(
            await asyncio.gather(
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
