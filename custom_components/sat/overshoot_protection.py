import asyncio
import logging
from collections import deque

from custom_components.sat import SatDataUpdateCoordinator
from .const import *

_LOGGER = logging.getLogger(__name__)

OVERSHOOT_PROTECTION_SETPOINT = 75
OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD = 0.00
OVERSHOOT_PROTECTION_ERROR_RELATIVE_MOD = 0.01
OVERSHOOT_PROTECTION_TIMEOUT = 7200  # 2 hours in seconds
OVERSHOOT_PROTECTION_INITIAL_WAIT = 120  # 2 minutes in seconds


class OvershootProtection:
    def __init__(self, coordinator: SatDataUpdateCoordinator):
        self._coordinator = coordinator

    async def calculate(self) -> float | None:
        _LOGGER.info("Starting calculation")
        await self._coordinator.api.set_ch_enable_bit(1)
        await self._coordinator.api.set_max_ch_setpoint(OVERSHOOT_PROTECTION_SETPOINT)
        await self._coordinator.api.set_control_setpoint(OVERSHOOT_PROTECTION_SETPOINT)
        await self._coordinator.api.set_max_relative_mod(OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD)

        try:
            # First wait for a flame
            await asyncio.wait_for(self._wait_for_flame(), timeout=OVERSHOOT_PROTECTION_TIMEOUT)

            # First run start_with_zero_modulation for at least 2 minutes
            _LOGGER.info("Running calculation with zero modulation")
            start_with_zero_modulation_task = asyncio.create_task(self._calculate_with_zero_modulation())
            await asyncio.sleep(OVERSHOOT_PROTECTION_INITIAL_WAIT)

            # Check if relative modulation is still zero
            if float(self._coordinator.get(gw_vars.DATA_REL_MOD_LEVEL)) == OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD:
                return await start_with_zero_modulation_task
            else:
                start_with_zero_modulation_task.cancel()
                _LOGGER.info("Relative modulation is not zero, switching to with modulation")
                return await self._calculate_with_modulation()

        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")
            return None

    async def _calculate_with_zero_modulation(self) -> float:
        try:
            return await asyncio.wait_for(
                self._wait_for_stable_temperature(OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD),
                timeout=OVERSHOOT_PROTECTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")

    async def _calculate_with_modulation(self) -> float:
        try:
            return await asyncio.wait_for(
                self._wait_for_stable_temperature(OVERSHOOT_PROTECTION_ERROR_RELATIVE_MOD),
                timeout=OVERSHOOT_PROTECTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")

    async def _wait_for_flame(self):
        previous_temp = None

        while True:
            actual_temp = float(self._coordinator.get(gw_vars.DATA_CH_WATER_TEMP))

            if previous_temp is not None:
                if bool(self._coordinator.get(gw_vars.DATA_SLAVE_FLAME_ON)) and abs(actual_temp - previous_temp) >= 0.75:
                    _LOGGER.info("Heating system has started to run")
                    break

            _LOGGER.warning("Heating system is not running yet")

            previous_temp = actual_temp
            await asyncio.sleep(10)

    async def _wait_for_temperature_rise(self):
        previous_temp = None

        while True:
            actual_temp = float(self._coordinator.get(gw_vars.DATA_CH_WATER_TEMP))

            if previous_temp is not None:
                if abs(actual_temp - previous_temp) >= 0.5:
                    break

            previous_temp = actual_temp
            await asyncio.sleep(30)

    async def _wait_for_stable_temperature(self, max_modulation: float) -> float:
        temps = deque(maxlen=10)
        previous_average_temp = None

        while True:
            actual_temp = float(self._coordinator.get(gw_vars.DATA_CH_WATER_TEMP))

            temps.append(actual_temp)
            average_temp = sum(temps) / len(temps)

            if previous_average_temp is not None:
                if abs(actual_temp - previous_average_temp) <= 0.1:
                    _LOGGER.info("Stable temperature reached: %s", actual_temp)
                    return actual_temp

            if max_modulation != OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD:
                await self._coordinator.api.set_control_setpoint(actual_temp)

            previous_average_temp = average_temp
            await asyncio.sleep(3)
