import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, SERVICE_RESET_INTEGRAL
from .util import get_climate_entities

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_register_services(hass: HomeAssistant) -> None:
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
