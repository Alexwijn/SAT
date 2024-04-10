import asyncio
import logging

from custom_components.sat.const import *
from custom_components.sat.coordinator import DeviceState, SatDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

OVERSHOOT_PROTECTION_TIMEOUT = 7200  # Two hours in seconds
OVERSHOOT_PROTECTION_INITIAL_WAIT = 180  # Three minutes in seconds


class OvershootProtection:
    def __init__(self, coordinator: SatDataUpdateCoordinator):
        self._alpha = 0.2
        self._coordinator = coordinator

    async def calculate(self) -> float | None:
        _LOGGER.info("Starting calculation")

        await self._coordinator.async_set_heater_state(DeviceState.ON)
        await self._coordinator.async_set_control_setpoint(OVERSHOOT_PROTECTION_SETPOINT)
        await self._coordinator.async_set_control_max_relative_modulation(MINIMUM_RELATIVE_MOD)

        try:
            # First wait for a flame
            await asyncio.wait_for(self._wait_for_flame(), timeout=OVERSHOOT_PROTECTION_INITIAL_WAIT)

            # Then we wait for 60 seconds more, so we at least make sure we have some change in temperature
            await asyncio.sleep(60)

            # Since the coordinator doesn't support modulation management, so we need to fall back to find it with modulation
            if not self._coordinator.supports_relative_modulation_management or self._coordinator.relative_modulation_value > 0:
                return await self._calculate_with_no_modulation_management()

            # Run with maximum power of the boiler, zero modulation.
            return await self._calculate_with_zero_modulation()
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")
            return None
        except asyncio.CancelledError as exception:
            await self._coordinator.async_set_heater_state(DeviceState.OFF)
            await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)
            await self._coordinator.async_set_control_max_relative_modulation(MAXIMUM_RELATIVE_MOD)

            raise exception

    async def _calculate_with_zero_modulation(self) -> float:
        _LOGGER.info("Running calculation with zero modulation")

        try:
            return await asyncio.wait_for(
                self._wait_for_stable_temperature(0),
                timeout=OVERSHOOT_PROTECTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")

    async def _calculate_with_no_modulation_management(self) -> float:
        _LOGGER.info("Running calculation with no modulation management")

        try:
            return await asyncio.wait_for(
                self._wait_for_stable_temperature(100),
                timeout=OVERSHOOT_PROTECTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for stable temperature")

    async def _wait_for_flame(self):
        while True:
            if bool(self._coordinator.flame_active):
                _LOGGER.info("Heating system has started to run")
                break

            _LOGGER.warning("Heating system is not running yet")
            await self._coordinator.async_set_control_setpoint(OVERSHOOT_PROTECTION_SETPOINT)

            await asyncio.sleep(5)
            await self._coordinator.async_control_heating_loop()

    async def _wait_for_stable_temperature(self, max_modulation: float) -> float:
        previous_average_temperature = float(self._coordinator.boiler_temperature)

        while True:
            actual_temperature = float(self._coordinator.boiler_temperature)
            average_temperature = self._alpha * actual_temperature + (1 - self._alpha) * previous_average_temperature

            if previous_average_temperature is not None and abs(actual_temperature - previous_average_temperature) <= DEADBAND:
                _LOGGER.info("Stable temperature reached: %s", actual_temperature)
                return actual_temperature

            previous_average_temperature = average_temperature

            if max_modulation > 0:
                await self._coordinator.async_set_control_setpoint(actual_temperature)
            else:
                await self._coordinator.async_set_control_setpoint(OVERSHOOT_PROTECTION_SETPOINT)

            await asyncio.sleep(2)
            await self._coordinator.async_control_heating_loop()
