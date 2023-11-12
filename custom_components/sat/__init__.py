import asyncio
import logging

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from . import mqtt, serial, switch
from .const import *
from .coordinator import SatDataUpdateCoordinatorFactory

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _entry: ConfigEntry):
    """
    Set up this integration using the UI.

    This function is called by Home Assistant when the integration is set up with the UI.
    """
    # Make sure we have our default domain property
    _hass.data.setdefault(DOMAIN, {})

    # Create a new dictionary for this entry
    _hass.data[DOMAIN][_entry.entry_id] = {}

    # Resolve the coordinator by using the factory according to the mode
    _hass.data[DOMAIN][_entry.entry_id][COORDINATOR] = await SatDataUpdateCoordinatorFactory().resolve(
        hass=_hass, config_entry=_entry, mode=_entry.data.get(CONF_MODE), device=_entry.data.get(CONF_DEVICE)
    )

    # Forward entry setup for climate and other platforms
    await _hass.async_add_job(_hass.config_entries.async_forward_entry_setup(_entry, CLIMATE_DOMAIN))
    await _hass.async_add_job(_hass.config_entries.async_forward_entry_setups(_entry, [SENSOR_DOMAIN, NUMBER_DOMAIN, BINARY_SENSOR_DOMAIN]))

    # Add an update listener for this entry
    _entry.async_on_unload(_entry.add_update_listener(async_reload_entry))

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


async def async_migrate_entry(_hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    from custom_components.sat.config_flow import SatFlowHandler
    _LOGGER.debug("Migrating from version %s", _entry.version)

    if _entry.version < SatFlowHandler.VERSION:
        new_data = {**_entry.data}
        new_options = {**_entry.options}

        if _entry.version < 2:
            if not _entry.data.get(CONF_MINIMUM_SETPOINT):
                # Legacy Store
                store = Store(_hass, 1, DOMAIN)
                new_data[CONF_MINIMUM_SETPOINT] = MINIMUM_SETPOINT

                if (data := await store.async_load()) and (overshoot_protection_value := data.get("overshoot_protection_value")):
                    new_data[CONF_MINIMUM_SETPOINT] = overshoot_protection_value

            if _entry.options.get("heating_system") == "underfloor":
                new_data[CONF_HEATING_SYSTEM] = HEATING_SYSTEM_UNDERFLOOR
            else:
                new_data[CONF_HEATING_SYSTEM] = HEATING_SYSTEM_RADIATORS

            if not _entry.data.get(CONF_MAXIMUM_SETPOINT):
                new_data[CONF_MAXIMUM_SETPOINT] = 55

                if _entry.options.get("heating_system") == "underfloor":
                    new_data[CONF_MAXIMUM_SETPOINT] = 50

                if _entry.options.get("heating_system") == "radiator_low_temperatures":
                    new_data[CONF_MAXIMUM_SETPOINT] = 55

                if _entry.options.get("heating_system") == "radiator_medium_temperatures":
                    new_data[CONF_MAXIMUM_SETPOINT] = 65

                if _entry.options.get("heating_system") == "radiator_high_temperatures":
                    new_data[CONF_MAXIMUM_SETPOINT] = 75

        if _entry.version < 3:
            if main_climates := _entry.options.get("main_climates"):
                new_data[CONF_MAIN_CLIMATES] = main_climates
                new_options.pop("main_climates")

            if secondary_climates := _entry.options.get("climates"):
                new_data[CONF_SECONDARY_CLIMATES] = secondary_climates
                new_options.pop("climates")

            if sync_with_thermostat := _entry.options.get("sync_with_thermostat"):
                new_data[CONF_SYNC_WITH_THERMOSTAT] = sync_with_thermostat
                new_options.pop("sync_with_thermostat")

        _entry.version = SatFlowHandler.VERSION
        _hass.config_entries.async_update_entry(_entry, data=new_data, options=new_options)

    _LOGGER.info("Migration to version %s successful", _entry.version)

    return True
