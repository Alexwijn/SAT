import asyncio
import logging
from collections import deque

from .coordinator import SatOpenThermCoordinator
from ..const import *

SOLUTION_AUTOMATIC = "auto"
SOLUTION_WITH_MODULATION = "with_modulation"
SOLUTION_WITH_ZERO_MODULATION = "with_zero_modulation"

_LOGGER = logging.getLogger(__name__)

OVERSHOOT_PROTECTION_SETPOINT = 75
OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD = 0.00
OVERSHOOT_PROTECTION_ERROR_RELATIVE_MOD = 0.01
OVERSHOOT_PROTECTION_TIMEOUT = 7200  # 2 hours in seconds
OVERSHOOT_PROTECTION_INITIAL_WAIT = 120  # 2 minutes in seconds


class OvershootProtection:
    def __init__(self, coordinator: SatOpenThermCoordinator):
        self._coordinator = coordinator

    async def calculate(self, solution: str) -> float | None:
        _LOGGER.info("Starting calculation")
        await self._coordinator.api.set_ch_enable_bit(1)
        await self._coordinator.api.set_max_ch_setpoint(OVERSHOOT_PROTECTION_SETPOINT)
        await self._coordinator.api.set_control_setpoint(OVERSHOOT_PROTECTION_SETPOINT)

        try:
            # First wait for a flame
            await asyncio.wait_for(self._wait_for_flame(), timeout=OVERSHOOT_PROTECTION_TIMEOUT)

            if solution == SOLUTION_AUTOMATIC:
                # First run start_with_zero_modulation for at least 2 minutes
                start_with_zero_modulation_task = asyncio.create_task(self._calculate_with_zero_modulation())
                await asyncio.sleep(OVERSHOOT_PROTECTION_INITIAL_WAIT)

                # Check if relative modulation is still zero
                if float(self._coordinator.get(gw_vars.DATA_REL_MOD_LEVEL)) == OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD:
                    return await start_with_zero_modulation_task
                else:
                    start_with_zero_modulation_task.cancel()
                    _LOGGER.info("Relative modulation is not zero, switching to with modulation")
                    return await self._calculate_with_modulation()
            elif solution == SOLUTION_WITH_MODULATION:
                return await self._calculate_with_modulation()
            elif solution == SOLUTION_WITH_ZERO_MODULATION:
                return await self._calculate_with_zero_modulation()
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")
            return None

    async def _calculate_with_zero_modulation(self) -> float:
        _LOGGER.info("Running calculation with zero modulation")
        await self._coordinator.api.set_max_relative_mod(OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD)

        try:
            return await asyncio.wait_for(
                self._wait_for_stable_temperature(OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD),
                timeout=OVERSHOOT_PROTECTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")

    async def _calculate_with_modulation(self) -> float:
        _LOGGER.info("Running calculation with modulation")

        try:
            return await asyncio.wait_for(
                self._wait_for_stable_temperature(OVERSHOOT_PROTECTION_ERROR_RELATIVE_MOD),
                timeout=OVERSHOOT_PROTECTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")

    async def _wait_for_flame(self):
        while True:
            if bool(self._coordinator.get(gw_vars.DATA_SLAVE_FLAME_ON)):
                _LOGGER.info("Heating system has started to run")
                break

            _LOGGER.warning("Heating system is not running yet")
            await asyncio.sleep(5)

    async def _wait_for_stable_temperature(self, max_modulation: float) -> float:
        temps = deque(maxlen=50)
        previous_average_temp = None

        while True:
            actual_temp = float(self._coordinator.get(gw_vars.DATA_CH_WATER_TEMP))

            temps.append(actual_temp)
            average_temp = sum(temps) / 50

            if previous_average_temp is not None:
                if abs(actual_temp - previous_average_temp) <= 0.1:
                    _LOGGER.info("Stable temperature reached: %s", actual_temp)
                    return actual_temp

            if max_modulation != OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD:
                await self._coordinator.api.set_control_setpoint(actual_temp)

            previous_average_temp = average_temp
            await asyncio.sleep(3)
