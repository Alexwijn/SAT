"""SatEntity class"""
import logging

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, VERSION, CONF_ID

_LOGGER: logging.Logger = logging.getLogger(__package__)


class SatEntity(CoordinatorEntity):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)

        self._config_entry = config_entry

    @property
    def device_info(self):
        return {
            "name": NAME,
            "model": VERSION,
            "manufacturer": NAME,
            "identifiers": {(DOMAIN, self._config_entry.data.get(CONF_ID))},
        }
