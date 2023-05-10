from __future__ import annotations

import logging
from typing import Optional, Any

from homeassistant.core import HomeAssistant
from pyotgw import OpenThermGateway
from pyotgw.vars import *

from ..config_store import SatConfigStore
from ..const import *
from ..coordinator import DeviceState, SatDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatSerialCoordinator(SatDataUpdateCoordinator):
    """Class to manage fetching data from the OTGW Gateway using pyotgw."""

    def __init__(self, hass: HomeAssistant, store: SatConfigStore, client: OpenThermGateway) -> None:
        """Initialize."""
        super().__init__(hass, store)

        self.api = client
        self.api.subscribe(self.async_set_updated_data)

    @property
    def device_active(self) -> bool:
        return bool(self.get(DATA_MASTER_CH_ENABLED) or False)

    @property
    def hot_water_active(self) -> bool:
        return bool(self.get(DATA_SLAVE_DHW_ACTIVE) or False)

    @property
    def supports_setpoint_management(self) -> bool:
        return True

    @property
    def supports_hot_water_setpoint_management(self):
        return True

    @property
    def supports_maximum_setpoint_management(self) -> bool:
        return True

    @property
    def support_relative_modulation_management(self) -> bool:
        return self._overshoot_protection or not self._force_pulse_width_modulation

    @property
    def setpoint(self) -> float | None:
        if (setpoint := self.get(DATA_CONTROL_SETPOINT)) is not None:
            return float(setpoint)

        return None

    @property
    def hot_water_setpoint(self) -> float | None:
        if (setpoint := self.get(DATA_DHW_SETPOINT)) is not None:
            return float(setpoint)

        return super().hot_water_setpoint

    @property
    def boiler_temperature(self) -> float | None:
        if (value := self.get(DATA_CH_WATER_TEMP)) is not None:
            return float(value)

        return super().boiler_temperature

    @property
    def minimum_hot_water_setpoint(self) -> float:
        if (setpoint := self.get(DATA_SLAVE_DHW_MIN_SETP)) is not None:
            return float(setpoint)

        return super().minimum_hot_water_setpoint

    @property
    def maximum_hot_water_setpoint(self) -> float | None:
        if (setpoint := self.get(DATA_SLAVE_DHW_MAX_SETP)) is not None:
            return float(setpoint)

        return super().maximum_hot_water_setpoint

    @property
    def relative_modulation_value(self) -> float | None:
        if (value := self.get(DATA_REL_MOD_LEVEL)) is not None:
            return float(value)

        return super().relative_modulation_value

    @property
    def boiler_capacity(self) -> float | None:
        if (value := self.get(DATA_SLAVE_MAX_CAPACITY)) is not None:
            return float(value)

        return super().boiler_capacity

    @property
    def minimum_relative_modulation_value(self) -> float | None:
        if (value := self.get(DATA_SLAVE_MIN_MOD_LEVEL)) is not None:
            return float(value)

        return super().minimum_relative_modulation_value

    @property
    def flame_active(self) -> bool:
        return bool(self.get(DATA_SLAVE_FLAME_ON))

    @property
    def minimum_setpoint(self):
        return self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE, self._minimum_setpoint)

    def get(self, key: str) -> Optional[Any]:
        """Get the value for the given `key` from the boiler data.

        :param key: Key of the value to retrieve from the boiler data.
        :return: Value for the given key from the boiler data, or None if the boiler data or the value are not available.
        """
        return self.data[BOILER].get(key) if self.data[BOILER] else None

    async def async_cleanup(self) -> None:
        self.api.unsubscribe(self.async_set_updated_data)

        await self.api.set_control_setpoint(0)
        await self.api.set_max_relative_mod("-")
        await self.api.disconnect()

    async def async_set_control_setpoint(self, value: float) -> None:
        if not self._simulation:
            await self.api.set_control_setpoint(value)

        await super().async_set_control_setpoint(value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        """Control the state of the central heating."""
        if not self._simulation:
            await self.api.set_ch_enable_bit(1 if state == DeviceState.ON else 0)

        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: float) -> None:
        if not self._simulation:
            await self.api.set_max_relative_mod(value)

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        """Set a maximum temperature limit on the boiler."""
        if not self._simulation:
            await self.api.set_max_ch_setpoint(value)

        await super().async_set_control_max_setpoint(value)
