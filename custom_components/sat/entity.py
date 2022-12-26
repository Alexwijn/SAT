"""SatEntity class"""
import logging

import pyotgw.vars as gw_vars
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, CONF_NAME

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatEntity(CoordinatorEntity):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)

        self._config_entry = config_entry

    @property
    def device_info(self):
        return {
            "manufacturer": NAME,
            "name": self.coordinator.data[gw_vars.OTGW][gw_vars.OTGW_ABOUT],
            "model": self.coordinator.data[gw_vars.OTGW][gw_vars.OTGW_BUILD],
            "identifiers": {(DOMAIN, self._config_entry.data.get(CONF_NAME))},
        }
