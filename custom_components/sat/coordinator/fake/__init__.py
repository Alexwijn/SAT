from __future__ import annotations

import logging
from typing import Optional

from homeassistant.core import HomeAssistant

from .. import SatDataUpdateCoordinator
from ...entry_data import SatConfig
from ...types import HeaterState

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatFakeConfig:
    def __init__(self) -> None:
        self.supports_setpoint_management = True
        self.supports_maximum_setpoint_management = False
        self.supports_hot_water_setpoint_management = False
        self.supports_relative_modulation_management = False


class SatFakeCoordinator(SatDataUpdateCoordinator):
    @property
    def id(self) -> str:
        return "Fake"

    @property
    def type(self) -> str:
        return "Fake"

    @property
    def member_id(self) -> Optional[int]:
        return -1

    def __init__(self, hass: HomeAssistant, config: SatConfig) -> None:
        self.config = SatFakeConfig()

        self._setpoint = None
        self._maximum_setpoint = None
        self._hot_water_setpoint = None
        self._boiler_temperature = None
        self._device_state = HeaterState.OFF
        self._relative_modulation_value = 100

        super().__init__(hass, config)

    @property
    def setpoint(self) -> Optional[float]:
        return self._setpoint

    @property
    def boiler_temperature(self) -> Optional[float]:
        return self._boiler_temperature

    @property
    def active(self) -> bool:
        return self._device_state == HeaterState.ON

    @property
    def relative_modulation_value(self):
        return self._relative_modulation_value

    @property
    def supports_setpoint_management(self):
        if self.config is None:
            return super().supports_setpoint_management

        return self.config.supports_setpoint_management

    @property
    def supports_hot_water_setpoint_management(self):
        if self.config is None:
            return super().supports_hot_water_setpoint_management

        return self.config.supports_hot_water_setpoint_management

    @property
    def supports_maximum_setpoint_management(self):
        if self.config is None:
            return super().supports_maximum_setpoint_management

        return self.config.supports_maximum_setpoint_management

    @property
    def supports_relative_modulation_management(self):
        if self.config is None:
            return super().supports_relative_modulation_management

        return self.config.supports_relative_modulation_management

    @property
    def supports_relative_modulation(self):
        return self.supports_relative_modulation_management

    async def async_set_boiler_temperature(self, value: float) -> None:
        self._boiler_temperature = value

    async def async_set_heater_state(self, state: HeaterState) -> None:
        self._device_state = state

        await super().async_set_heater_state(state)

    async def async_set_control_setpoint(self, value: float) -> None:
        self._setpoint = value

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        self._hot_water_setpoint = value

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        self._relative_modulation_value = value

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        self._maximum_setpoint = value

        await super().async_set_control_max_setpoint(value)
