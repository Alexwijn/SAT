import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, entity_registry

from .const import DOMAIN, SERVICE_PULSE_WIDTH_MODULATION, SERVICE_RESET_INTEGRAL
from .entry_data import SatEntryData

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_register_services(hass: HomeAssistant) -> None:
    def get_entry_data_for_entities(hass: "HomeAssistant", entity_ids: list[str]) -> list["SatEntryData"]:
        """Retrieve the entry data for the given climate entity IDs."""
        entries = []
        for entity_id in entity_ids:
            registry = entity_registry.async_get(hass)

            if not (entry := registry.async_get(entity_id)):
                continue

            if not (entry_data := hass.data.get(DOMAIN, {}).get(entry.config_entry_id)):
                continue

            entries.append(entry_data)

        return entries

    async def reset_integral(call: ServiceCall):
        """Service to reset the integral part of the PID controller."""
        for entry_data in get_entry_data_for_entities(hass, call.data.get("entity_id", [])):
            if (climate := entry_data.climate) is None:
                continue

            _LOGGER.info("Reset Integral action called for %s", climate.entity_id)

            climate.pid.reset()
            climate.areas.pids.reset()

    async def pulse_width_modulation(call: ServiceCall):
        """Service to enable or disable Pulse Width Modulation."""
        enabled = call.data.get("enabled")

        for entry_data in get_entry_data_for_entities(hass, call.data.get("entity_id", [])):
            _LOGGER.info("Pulse Width Modulation action called for %s with enabled=%s", entry_data.config.name, enabled)

            if enabled:
                entry_data.heating_control.enable_pwm()
            else:
                entry_data.heating_control.disable_pwm()

    hass.services.async_register(
        DOMAIN,
        service=SERVICE_RESET_INTEGRAL,
        service_func=reset_integral,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_ids})
    )

    hass.services.async_register(
        DOMAIN,
        service=SERVICE_PULSE_WIDTH_MODULATION,
        service_func=pulse_width_modulation,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_ids, vol.Required("enabled"): cv.boolean})
    )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the SAT services when the last entry unloads."""
    hass.services.async_remove(DOMAIN, SERVICE_RESET_INTEGRAL)
    hass.services.async_remove(DOMAIN, SERVICE_PULSE_WIDTH_MODULATION)
