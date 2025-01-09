from __future__ import annotations

import asyncio
import logging
from typing import Optional, Any, Mapping

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from pyotgw import vars as gw_vars, OpenThermGateway
from pyotgw.vars import *
from serial import SerialException

from ..coordinator import DeviceState, SatDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__name__)

# Sensors
TRANSLATE_SOURCE = {
    gw_vars.OTGW: None,
    gw_vars.BOILER: "Boiler",
    gw_vars.THERMOSTAT: "Thermostat",
}


class SatSerialCoordinator(SatDataUpdateCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using pyotgw."""

    def __init__(self, hass: HomeAssistant, port: str, data: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> None:
        """Initialize."""
        super().__init__(hass, data, options)

        self.data = DEFAULT_STATUS

        async def async_coroutine(event):
            self.async_set_updated_data(event)

        self._port = port
        self._api = OpenThermGateway()
        self._api.subscribe(async_coroutine)

    @property
    def device_id(self) -> str:
        return self._port

    @property
    def device_type(self) -> str:
        return "OpenThermGateway (via serial)"

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
    def supports_relative_modulation_management(self) -> bool:
        return True

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
    def return_temperature(self) -> float | None:
        if (value := self.get(DATA_RETURN_WATER_TEMP)) is not None:
            return float(value)

        return super().return_temperature

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
    def maximum_relative_modulation_value(self) -> float | None:
        if (value := self.get(DATA_SLAVE_MAX_RELATIVE_MOD)) is not None:
            return float(value)

        return super().maximum_relative_modulation_value

    @property
    def member_id(self) -> int | None:
        if (value := self.get(DATA_SLAVE_MEMBERID)) is not None:
            return int(value)

        return None

    @property
    def flame_active(self) -> bool:
        return bool(self.get(DATA_SLAVE_FLAME_ON))

    def get(self, key: str) -> Optional[Any]:
        """Get the value for the given `key` from the boiler data.

        :param key: Key of the value to retrieve from the boiler data.
        :return: Value for the given key from the boiler data, or None if the boiler data or the value are not available.
        """
        return self.data[BOILER].get(key)

    async def async_connect(self) -> SatSerialCoordinator:
        try:
            await self._api.connect(port=int(self._port), timeout=5)
        except (asyncio.TimeoutError, ConnectionError, SerialException) as exception:
            raise ConfigEntryNotReady(f"Could not connect to gateway at {self._port}: {exception}") from exception

        return self

    async def async_setup(self) -> None:
        await self.async_connect()

    async def async_will_remove_from_hass(self) -> None:
        self._api.unsubscribe(self.async_set_updated_data)

        await self._api.set_control_setpoint(0)
        await self._api.set_max_relative_mod("-")
        await self._api.disconnect()

    async def async_set_control_setpoint(self, value: float) -> None:
        if not self._simulation:
            await self._api.set_control_setpoint(value)

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        if not self._simulation:
            await self._api.set_dhw_setpoint(value)

        await super().async_set_control_thermostat_setpoint(value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        if not self._simulation:
            await self._api.set_target_temp(value)

        await super().async_set_control_thermostat_setpoint(value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        if not self._simulation:
            await self._api.set_ch_enable_bit(1 if state == DeviceState.ON else 0)

        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        if not self._simulation:
            await self._api.set_max_relative_mod(value)

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        if not self._simulation:
            await self._api.set_max_ch_setpoint(value)

        await super().async_set_control_max_setpoint(value)
