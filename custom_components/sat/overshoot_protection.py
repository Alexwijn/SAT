import asyncio
import logging

from custom_components.sat.const import *
from custom_components.sat.coordinator import DeviceState, SatDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

OVERSHOOT_PROTECTION_TIMEOUT = 7200  # Two hours in seconds
OVERSHOOT_PROTECTION_INITIAL_WAIT = 180  # Three minutes in seconds


class OvershootProtection:
    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_system: str):
        self._alpha = 0.2
        self._coordinator = coordinator
        self._setpoint = OVERSHOOT_PROTECTION_SETPOINT[heating_system]

    async def calculate(self) -> float | None:
        try:
            _LOGGER.info("Starting calculation")

            # Turn on the heater
            await self._coordinator.async_set_heater_state(DeviceState.ON)

            # First wait for a flame
            await asyncio.wait_for(self._wait_for_flame(), timeout=OVERSHOOT_PROTECTION_INITIAL_WAIT)

            # Then we wait until we reach the target setpoint temperature
            await asyncio.wait_for(self._wait_for_stable_temperature(), timeout=OVERSHOOT_PROTECTION_TIMEOUT)

            # Then we wait for 5 minutes more, so we at least make sure we are stable
            await asyncio.sleep(300)

            # Then we wait for a stable relative modulation value
            relative_modulation_value = await asyncio.wait_for(self._wait_for_relative_modulation(), timeout=OVERSHOOT_PROTECTION_TIMEOUT)

            # Calculate the new overshoot protection value
            return (100 - relative_modulation_value / 100) * self._setpoint
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for calculation")
            return None
        except asyncio.CancelledError as exception:
            await self._coordinator.async_set_heater_state(DeviceState.OFF)
            await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)

            raise exception

    async def _wait_for_flame(self) -> None:
        while True:
            if bool(self._coordinator.flame_active):
                _LOGGER.info("Heating system has started to run")
                break

            _LOGGER.warning("Heating system is not running yet")
            await self._coordinator.async_set_control_setpoint(self._setpoint)

            await asyncio.sleep(5)
            await self._coordinator.async_control_heating_loop()

    async def _wait_for_stable_temperature(self) -> None:
        previous_average_temperature = float(self._coordinator.boiler_temperature)

        while True:
            actual_temperature = float(self._coordinator.boiler_temperature)
            average_temperature = self._alpha * actual_temperature + (1 - self._alpha) * previous_average_temperature
            error_value = abs(actual_temperature - previous_average_temperature)

            if previous_average_temperature is not None and error_value <= DEADBAND:
                _LOGGER.info("Stable temperature reached: %s", actual_temperature)
                break

            previous_average_temperature = average_temperature
            await self._coordinator.async_set_control_setpoint(self._setpoint)
            await self._coordinator.async_set_control_max_relative_modulation(MAXIMUM_RELATIVE_MOD)

            await asyncio.sleep(5)
            await self._coordinator.async_control_heating_loop()
            _LOGGER.info("Current temperature: %s, error: %s", actual_temperature, error_value)

    async def _wait_for_relative_modulation(self) -> float:
        previous_average_value = float(self._coordinator.relative_modulation_value)

        while True:
            actual_value = float(self._coordinator.relative_modulation_value)
            average_value = self._alpha * actual_value + (1 - self._alpha) * previous_average_value
            error_value = abs(actual_value - previous_average_value)

            if previous_average_value is not None and error_value <= DEADBAND:
                _LOGGER.info("Relative Modulation reached: %s", actual_value)
                return actual_value

            previous_average_value = average_value
            await self._coordinator.async_set_control_setpoint(self._setpoint)
            await self._coordinator.async_set_control_max_relative_modulation(MAXIMUM_RELATIVE_MOD)

            await asyncio.sleep(5)
            await self._coordinator.async_control_heating_loop()
            _LOGGER.info("Relative Modulation: %s, error: %s", actual_value, error_value)
