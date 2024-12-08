import asyncio
import logging

from custom_components.sat.const import *
from custom_components.sat.coordinator import DeviceState, SatDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

OVERSHOOT_PROTECTION_TIMEOUT = 7200  # Two hours in seconds
OVERSHOOT_PROTECTION_INITIAL_WAIT = 180  # Three minutes in seconds
STABLE_TEMPERATURE_WAIT = 300  # Five minutes in seconds
SLEEP_INTERVAL = 5  # Sleep interval in seconds


class OvershootProtection:
    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_system: str):
        self._alpha = 0.2
        self._coordinator = coordinator
        self._setpoint = OVERSHOOT_PROTECTION_SETPOINT[heating_system]

    async def calculate(self) -> float | None:
        try:
            _LOGGER.info("Starting overshoot protection calculation")

            # Enforce timeouts to ensure operations do not run indefinitely
            await asyncio.wait_for(self._wait_for_flame(), timeout=OVERSHOOT_PROTECTION_INITIAL_WAIT)
            await asyncio.wait_for(self._wait_for_stable_temperature(), timeout=STABLE_TEMPERATURE_WAIT)

            relative_modulation_value = await asyncio.wait_for(self._wait_for_stable_relative_modulation(), timeout=OVERSHOOT_PROTECTION_TIMEOUT)

            return self._calculate_overshoot_value(relative_modulation_value)
        except asyncio.TimeoutError as exception:
            _LOGGER.warning("Timed out during overshoot protection calculation")

            raise exception
        except asyncio.CancelledError as exception:
            _LOGGER.info("Calculation cancelled, shutting down heating system")
            await self._coordinator.async_set_heater_state(DeviceState.OFF)
            await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)

            raise exception

    async def _wait_for_flame(self) -> None:
        while not bool(self._coordinator.flame_active):
            _LOGGER.warning("Waiting for heating system to start")
            await self._trigger_heating_cycle()

        _LOGGER.info("Heating system has started")

    async def _wait_for_stable_temperature(self) -> None:
        starting_temperature = float(self._coordinator.boiler_temperature)
        previous_average_temperature = float(self._coordinator.boiler_temperature)

        while True:
            current_temperature = float(self._coordinator.boiler_temperature)
            average_temperature, error_value = self._calculate_exponential_moving_average(previous_average_temperature, current_temperature)

            if current_temperature > starting_temperature and previous_average_temperature is not None and error_value <= DEADBAND:
                _LOGGER.info("Stable temperature reached: %s°C", current_temperature)
                return

            previous_average_temperature = average_temperature
            await self._trigger_heating_cycle()
            _LOGGER.debug("Temperature: %s°C, Error: %s°C", current_temperature, error_value)

    async def _wait_for_stable_relative_modulation(self) -> float:
        previous_average_value = float(self._coordinator.relative_modulation_value)

        while True:
            current_value = float(self._coordinator.relative_modulation_value)
            average_value, error_value = self._calculate_exponential_moving_average(previous_average_value, current_value)

            if previous_average_value is not None and error_value <= DEADBAND:
                _LOGGER.info("Stable relative modulation reached: %s%%", current_value)
                return current_value

            previous_average_value = average_value
            await self._trigger_heating_cycle()
            _LOGGER.debug("Relative Modulation: %s%%, Error: %s%%", current_value, error_value)

    def _calculate_overshoot_value(self, relative_modulation_value: float) -> float:
        overshoot_value = (100 - relative_modulation_value) / 100 * self._setpoint
        _LOGGER.info("Calculated overshoot value: %s", overshoot_value)
        return overshoot_value

    def _calculate_exponential_moving_average(self, previous_average: float, current_value: float) -> tuple[float, float]:
        average_value = self._alpha * current_value + (1 - self._alpha) * previous_average
        error_value = abs(current_value - previous_average)
        return average_value, error_value

    async def _trigger_heating_cycle(self) -> None:
        await self._coordinator.async_set_heater_state(DeviceState.ON)
        await self._coordinator.async_set_control_setpoint(self._setpoint)
        await self._coordinator.async_set_control_max_relative_modulation(MAXIMUM_RELATIVE_MOD)
        await asyncio.sleep(SLEEP_INTERVAL)
        await self._coordinator.async_control_heating_loop()
