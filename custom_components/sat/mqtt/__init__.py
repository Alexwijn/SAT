import logging

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import SatMqttCoordinator
from ..const import *

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _entry: ConfigEntry):
    store = _hass.data[DOMAIN][_entry.entry_id][CONFIG_STORE]
    _hass.data[DOMAIN][_entry.entry_id][COORDINATOR] = SatMqttCoordinator(_hass, store, _entry.data.get(CONF_DEVICE))

    await mqtt.async_wait_for_mqtt_client(_hass)
