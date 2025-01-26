from __future__ import annotations

import logging
import typing

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, CONF_NAME

_LOGGER: logging.Logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from .climate import SatClimate
    from .coordinator import SatDataUpdateCoordinator


class SatEntity(CoordinatorEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator)

        self._coordinator = coordinator
        self._config_entry = config_entry

    @property
    def device_info(self):
        manufacturer = "Unknown"
        if self._coordinator.manufacturer is not None:
            manufacturer = self._coordinator.manufacturer.friendly_name

        return DeviceInfo(
            name=NAME,
            manufacturer=manufacturer,
            suggested_area="Living Room",
            model=self._coordinator.device_type,
            identifiers={(DOMAIN, self._config_entry.data.get(CONF_NAME))}
        )


class SatClimateEntity(SatEntity):
    def __init__(self, coordinator, config_entry: ConfigEntry, climate: SatClimate):
        super().__init__(coordinator, config_entry)

        self._climate = climate
