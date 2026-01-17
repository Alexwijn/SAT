import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry

from .climate import SatClimate
from .const import DOMAIN, SERVICE_RESET_INTEGRAL

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_register_services(hass: HomeAssistant) -> None:
    def get_climate_entities(hass: "HomeAssistant", entity_ids: list[str]) -> list["SatClimate"]:
        """Retrieve climate entities for the given entity IDs."""
        entities = []
        for entity_id in entity_ids:
            registry = entity_registry.async_get(hass)

            if not (entry := registry.async_get(entity_id)):
                continue

            if not (entry_data := hass.data[DOMAIN].get(entry.config_entry_id)):
                continue

            if entry_data.climate is not None:
                entities.append(entry_data.climate)

        return entities

    async def reset_integral(call: ServiceCall):
        """Service to reset the integral part of the PID controller."""
        target_entities = call.data.get("entity_id", [])

        for climate in get_climate_entities(hass, target_entities):
            _LOGGER.info("Reset Integral action called for %s", climate.entity_id)

            climate.pid.reset()
            climate.areas.pids.reset()

    hass.services.async_register(
        DOMAIN,
        service=SERVICE_RESET_INTEGRAL,
        service_func=reset_integral,
        schema=vol.Schema({vol.Required("entity_id"): list[str]})
    )
