from __future__ import annotations, annotations

import logging

from homeassistant.core import HomeAssistant

from ..config_store import SatConfigStore
from ..coordinator import DeviceState, SatDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatFakeConfig:
    def __init__(
            self,
            supports_setpoint_management: bool = False,
            supports_maximum_setpoint_management: bool = False,
            supports_hot_water_setpoint_management: bool = False,
            supports_relative_modulation_management: bool = False
    ):
        self.supports_setpoint_management = supports_setpoint_management
        self.supports_maximum_setpoint_management = supports_maximum_setpoint_management
        self.supports_hot_water_setpoint_management = supports_hot_water_setpoint_management
        self.supports_relative_modulation_management = supports_relative_modulation_management


class SatFakeCoordinator(SatDataUpdateCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using mqtt."""

    def __init__(self, hass: HomeAssistant, store: SatConfigStore) -> None:
        self.data = {}
        self.config = None

        self._setpoint = None
        self._maximum_setpoint = None
        self._hot_water_setpoint = None
        self._relative_modulation_value = 100

        super().__init__(hass, store)

    @property
    def setpoint(self) -> float | None:
        return self._setpoint

    @property
    def device_active(self) -> bool:
        return self._device_state == DeviceState.ON

    @property
    def supports_setpoint_management(self):
        if self.config is None:
            return super().supports_setpoint_management

        return self.config.supports_maximum_setpoint_management

    @property
    def supports_hot_water_setpoint_management(self):
        if self.config is None:
            return super().supports_hot_water_setpoint_management

        return self.config.supports_hot_water_setpoint_management

    def supports_maximum_setpoint_management(self):
        if self.config is None:
            return super().supports_maximum_setpoint_management

        return self.config.supports_maximum_setpoint_management

    @property
    def supports_relative_modulation_management(self):
        if self.config is None:
            return super().supports_relative_modulation_management

        return self.config.supports_relative_modulation_management

    async def async_set_control_setpoint(self, value: float) -> None:
        self._setpoint = value

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        self._hot_water_setpoint = value

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_control_max_relative_modulation(self, value: float) -> None:
        self._relative_modulation_value = value

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        self._maximum_setpoint = value

        await super().async_set_control_max_setpoint(value)
