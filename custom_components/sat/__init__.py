import asyncio
import logging

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry
from homeassistant.helpers.storage import Store

from .const import *
from .coordinator import SatDataUpdateCoordinatorFactory

_LOGGER: logging.Logger = logging.getLogger(__name__)
PLATFORMS = [CLIMATE_DOMAIN, SENSOR_DOMAIN, NUMBER_DOMAIN, BINARY_SENSOR_DOMAIN]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """
    Set up this integration using the UI.

    This function is called by Home Assistant when the integration is set up with the UI.
    """
    # Make sure we have our default domain property
    hass.data.setdefault(DOMAIN, {})

    # Create a new dictionary for this entry
    hass.data[DOMAIN][entry.entry_id] = {}

    # Resolve the coordinator by using the factory according to the mode
    hass.data[DOMAIN][entry.entry_id][COORDINATOR] = await SatDataUpdateCoordinatorFactory().resolve(
        hass=hass, data=entry.data, options=entry.options, mode=entry.data.get(CONF_MODE), device=entry.data.get(CONF_DEVICE)
    )

    # Forward entry setup for used platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Add an update listener for this entry
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Handle removal of an entry.

    This function is called by Home Assistant when the integration is being removed.
    """

    climate = hass.data[DOMAIN][entry.entry_id][CLIMATE]
    await hass.data[DOMAIN][entry.entry_id][COORDINATOR].async_will_remove_from_hass(climate)

    unloaded = all(
        # Forward entry unload for used platforms
        await asyncio.gather(hass.config_entries.async_unload_platforms(entry, PLATFORMS))
    )

    # Remove the entry from the data dictionary if all components are unloaded successfully
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Reload config entry.

    This function is called by Home Assistant when the integration configuration is updated.
    """
    # Unload the entry and its dependent components
    await async_unload_entry(hass, entry)

    # Set up the entry again
    await async_setup_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    from .config_flow import SatFlowHandler
    _LOGGER.debug("Migrating from version %s", entry.version)

    if entry.version < SatFlowHandler.VERSION:
        new_data = {**entry.data}
        new_options = {**entry.options}

        if entry.version < 2:
            if not entry.data.get("minimum_setpoint"):
                # Legacy Store
                store = Store(hass, 1, DOMAIN)
                new_data["minimum_setpoint"] = 10

                if (data := await store.async_load()) and (overshoot_protection_value := data.get("overshoot_protection_value")):
                    new_data["minimum_setpoint"] = overshoot_protection_value

            if entry.options.get("heating_system") == "underfloor":
                new_data["heating_system"] = "underfloor"
            else:
                new_data["heating_system"] = "radiators"

            if not entry.data.get("maximum_setpoint"):
                new_data["maximum_setpoint"] = 55

                if entry.options.get("heating_system") == "underfloor":
                    new_data["maximum_setpoint"] = 50

                if entry.options.get("heating_system") == "radiator_low_temperatures":
                    new_data["maximum_setpoint"] = 55

                if entry.options.get("heating_system") == "radiator_medium_temperatures":
                    new_data["maximum_setpoint"] = 65

                if entry.options.get("heating_system") == "radiator_high_temperatures":
                    new_data["maximum_setpoint"] = 75

        if entry.version < 3:
            if main_climates := entry.options.get("main_climates"):
                new_data["main_climates"] = main_climates
                new_options.pop("main_climates")

            if secondary_climates := entry.options.get("climates"):
                new_data["secondary_climates"] = secondary_climates
                new_options.pop("climates")

            if sync_with_thermostat := entry.options.get("sync_with_thermostat"):
                new_data["sync_with_thermostat"] = sync_with_thermostat
                new_options.pop("sync_with_thermostat")

        if entry.version < 4:
            if entry.data.get("window_sensor") is not None:
                new_data["window_sensors"] = [entry.data.get("window_sensor")]
                del new_options["window_sensor"]

        if entry.version < 5:
            if entry.options.get("overshoot_protection") is not None:
                new_data["overshoot_protection"] = entry.options.get("overshoot_protection")
                del new_options["overshoot_protection"]

        if entry.version < 7:
            new_options["pid_controller_version"] = 1

        if entry.version < 8:
            if entry.options.get("heating_curve_version") is not None and int(entry.options.get("heating_curve_version")) < 2:
                new_options["heating_curve_version"] = 3

        if entry.version < 9:
            if entry.data.get("heating_system") == "heat_pump":
                new_options["cycles_per_hour"] = 2

            if entry.data.get("heating_system") == "radiators":
                new_options["cycles_per_hour"] = 3

        if entry.version < 10:
            if entry.data.get("mode") == "mqtt":
                device = device_registry.async_get(hass).async_get(entry.data.get("device"))

                new_data["mode"] = "mqtt_opentherm"
                new_data["device"] = list(device.identifiers)[0][1]

        hass.config_entries.async_update_entry(entry, version=SatFlowHandler.VERSION, data=new_data, options=new_options)

    _LOGGER.info("Migration to version %s successful", entry.version)

    return True
