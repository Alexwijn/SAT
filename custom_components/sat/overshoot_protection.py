import asyncio
import logging
from collections import deque

from custom_components.sat.const import *
from custom_components.sat.coordinator import DeviceState, SatDataUpdateCoordinator

SOLUTION_AUTOMATIC = "auto"
SOLUTION_WITH_MODULATION = "with_modulation"
SOLUTION_WITH_ZERO_MODULATION = "with_zero_modulation"

_LOGGER = logging.getLogger(__name__)

OVERSHOOT_PROTECTION_SETPOINT = 75
OVERSHOOT_PROTECTION_ERROR_RELATIVE_MOD = 0.01
OVERSHOOT_PROTECTION_TIMEOUT = 7200  # Two hours in seconds
OVERSHOOT_PROTECTION_INITIAL_WAIT = 120  # Two minutes in seconds


class OvershootProtection:
    def __init__(self, coordinator: SatDataUpdateCoordinator):
        self._coordinator = coordinator

    async def calculate(self, solution: str = SOLUTION_AUTOMATIC) -> float | None:
        _LOGGER.info("Starting calculation")

        await self._coordinator.async_set_heater_state(DeviceState.ON)

        try:
            # First wait for a flame
            await asyncio.wait_for(self._wait_for_flame(), timeout=OVERSHOOT_PROTECTION_INITIAL_WAIT)

            # Since the coordinator doesn't support modulation management, so we need to fall back to find it with modulation
            if solution == SOLUTION_AUTOMATIC and not self._coordinator.supports_relative_modulation_management:
                solution = SOLUTION_WITH_MODULATION
                _LOGGER.info("Relative modulation management is not supported, switching to with modulation")

            if solution == SOLUTION_AUTOMATIC:
                # Check if relative modulation is zero after the flame is on
                if float(self._coordinator.relative_modulation_value) == 0:
                    _LOGGER.info("Relative modulation is zero, starting with modulation")
                    return await self._calculate_with_zero_modulation()
                else:
                    _LOGGER.info("Relative modulation is not zero, starting with zero modulation")
                    return await self._calculate_with_zero_modulation()
            elif solution == SOLUTION_WITH_MODULATION:
                return await self._calculate_with_modulation()
            elif solution == SOLUTION_WITH_ZERO_MODULATION:
                return await self._calculate_with_zero_modulation()
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")
            return None
        except asyncio.CancelledError as ex:
            await self._coordinator.async_set_heater_state(DeviceState.OFF)
            await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)
            await self._coordinator.async_set_control_max_relative_modulation(MAXIMUM_RELATIVE_MOD)

            raise ex

    async def _calculate_with_zero_modulation(self) -> float:
        _LOGGER.info("Running calculation with zero modulation")
        await self._coordinator.async_set_control_max_relative_modulation(0)
        await self._coordinator.async_set_control_setpoint(OVERSHOOT_PROTECTION_SETPOINT)

        try:
            return await asyncio.wait_for(
                self._wait_for_stable_temperature(0),
                timeout=OVERSHOOT_PROTECTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")

    async def _calculate_with_modulation(self) -> float:
        _LOGGER.info("Running calculation with modulation")
        await self._coordinator.async_set_control_max_relative_modulation(100)
        await self._coordinator.async_set_control_setpoint(OVERSHOOT_PROTECTION_SETPOINT)

        try:
            return await asyncio.wait_for(
                self._wait_for_stable_temperature(100),
                timeout=OVERSHOOT_PROTECTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")

    async def _wait_for_flame(self):
        initial_setpoint = self._coordinator.boiler_temperature + 10

        while True:
            if bool(self._coordinator.flame_active):
                _LOGGER.info("Heating system has started to run")
                break

            _LOGGER.warning("Heating system is not running yet")
            await self._coordinator.async_set_control_setpoint(initial_setpoint)

            await asyncio.sleep(5)
            await self._coordinator.async_control_heating_loop()

    async def _wait_for_stable_temperature(self, max_modulation: float) -> float:
        temps = deque(maxlen=50)
        previous_average_temp = None

        while True:
            actual_temp = float(self._coordinator.boiler_temperature)

            temps.append(actual_temp)
            average_temp = sum(temps) / 50

            if previous_average_temp is not None:
                if abs(actual_temp - previous_average_temp) <= DEADBAND:
                    _LOGGER.info("Stable temperature reached: %s", actual_temp)
                    return actual_temp

            if max_modulation > 0:
                await self._coordinator.async_set_control_setpoint(actual_temp)
            else:
                await self._coordinator.async_set_control_setpoint(OVERSHOOT_PROTECTION_SETPOINT)

            previous_average_temp = average_temp

            await asyncio.sleep(3)
            await self._coordinator.async_control_heating_loop()
