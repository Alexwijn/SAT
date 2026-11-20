from __future__ import annotations

import logging
import typing

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SIGNAL_DEMAND_CONTROL_UPDATED, SIGNAL_HEATING_CONTROL_UPDATED
from .entry_data import SatConfig

_LOGGER: logging.Logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from .coordinator import SatDataUpdateCoordinator
    from .demand_control import SatDemandControl
    from .heating_control import SatHeatingControl


class SatEntity(CoordinatorEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, config: SatConfig, heating_control: SatHeatingControl):
        super().__init__(coordinator)

        self._coordinator: SatDataUpdateCoordinator = coordinator
        self._heating_control = heating_control
        self._config = config

    @property
    def device_info(self):
        manufacturer = "Unknown"
        if self._coordinator.manufacturer is not None:
            manufacturer = self._coordinator.manufacturer.friendly_name

        return DeviceInfo(
            name=self._config.name,
            manufacturer=manufacturer,
            model=self._coordinator.type,
            identifiers={(DOMAIN, self._config.entry_id)}
        )


class SatHeatingControlEntity(SatEntity):
    """Entity whose state derives from the heating control, refreshed once it has processed a device update."""

    async def async_added_to_hass(self) -> None:
        def on_heating_control_updated(device_id: str) -> None:
            if device_id != self._coordinator.id:
                return

            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_HEATING_CONTROL_UPDATED, on_heating_control_updated))


class SatDemandControlEntity(SatEntity):
    """Entity whose state derives from the demand control, refreshed whenever it reports a change."""

    def __init__(self, coordinator, config: SatConfig, heating_control: SatHeatingControl, demand_control: SatDemandControl):
        super().__init__(coordinator, config, heating_control)

        self._demand_control = demand_control

    async def async_added_to_hass(self) -> None:
        @callback
        def on_demand_control_updated(device_id: str) -> None:
            if device_id != self._coordinator.id:
                return

            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_DEMAND_CONTROL_UPDATED, on_demand_control_updated))
