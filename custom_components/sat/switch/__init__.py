import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import SatSwitchCoordinator
from ..const import *

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _entry: ConfigEntry):
    _LOGGER.debug("Setting up Switch integration")

    store = _hass.data[DOMAIN][_entry.entry_id][CONFIG_STORE]
    _hass.data[DOMAIN][_entry.entry_id] = {COORDINATOR: SatSwitchCoordinator(_hass, store)}
