import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from pyotgw import OpenThermGateway
from serial import SerialException

from .coordinator import SatOpenThermCoordinator
from ..const import *

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _entry: ConfigEntry):
    try:
        client = OpenThermGateway()
        await client.connect(port=_entry.data.get(CONF_DEVICE), timeout=5)
    except (asyncio.TimeoutError, ConnectionError, SerialException) as exception:
        raise ConfigEntryNotReady(f"Could not connect to gateway at {_entry.data.get(CONF_DEVICE)}: {exception}") from exception

    store = _hass.data[DOMAIN][_entry.entry_id][CONFIG_STORE]
    _hass.data[DOMAIN][_entry.entry_id][COORDINATOR] = SatOpenThermCoordinator(_hass, store, client)
