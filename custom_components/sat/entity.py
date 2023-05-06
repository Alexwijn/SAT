"""SatEntity class"""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, VERSION, CONF_NAME
from .coordinator import SatDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatEntity(CoordinatorEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator)

        self._coordinator = coordinator
        self._config_entry = config_entry

    @property
    def device_info(self):
        return {
            "name": NAME,
            "model": VERSION,
            "manufacturer": NAME,
            "identifiers": {(DOMAIN, self._config_entry.data.get(CONF_NAME))},
        }
