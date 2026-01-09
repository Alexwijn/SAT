from __future__ import annotations

import logging
import typing

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .entry_data import SatConfig

_LOGGER: logging.Logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from .climate import SatClimate
    from .coordinator import SatDataUpdateCoordinator


class SatEntity(CoordinatorEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, config: SatConfig):
        super().__init__(coordinator)

        self._coordinator: SatDataUpdateCoordinator = coordinator
        self._config = config

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
            identifiers={(DOMAIN, self._config.entry_id)}
        )


class SatClimateEntity(SatEntity):
    def __init__(self, coordinator, config: SatConfig, climate: SatClimate):
        super().__init__(coordinator, config)

        self._climate = climate

    async def async_added_to_hass(self) -> None:
        def on_state_change(_event):
            self.schedule_update_ha_state()

        await super().async_added_to_hass()

        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._climate.entity_id], on_state_change)
        )
