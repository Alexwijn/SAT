import asyncio
import logging

from .const import OVERSHOOT_PROTECTION_SETPOINT, MINIMUM_SETPOINT, DEADBAND, MAXIMUM_RELATIVE_MOD
from .coordinator import DeviceState, SatDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Constants for timeouts and intervals
OVERSHOOT_PROTECTION_TIMEOUT = 7200  # Two hours in seconds
OVERSHOOT_PROTECTION_INITIAL_WAIT = 300  # Five minutes in seconds
OVERSHOOT_PROTECTION_STABLE_WAIT = 900  # Fifteen minutes in seconds
SLEEP_INTERVAL = 30  # Sleep interval in seconds


class OvershootProtection:
    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_system: str):
        """Initialize OvershootProtection with a coordinator and heating system configuration."""
        self._alpha = 0.5
        self._coordinator = coordinator
        self._setpoint = OVERSHOOT_PROTECTION_SETPOINT.get(heating_system)

        if self._setpoint is None:
            raise ValueError(f"Invalid heating system: {heating_system}")

    async def calculate(self) -> float | None:
        """Calculate the overshoot protection value."""
        try:
            _LOGGER.info("Starting overshoot protection calculation")

            # Sequentially ensure the system is ready
            await asyncio.wait_for(self._wait_for_flame(), timeout=OVERSHOOT_PROTECTION_TIMEOUT)

            # Wait for a stable temperature
            relative_modulation_value = await asyncio.wait_for(self._wait_for_stable_relative_modulation(), timeout=OVERSHOOT_PROTECTION_STABLE_WAIT)
            await asyncio.wait_for(self._wait_for_stable_temperature(relative_modulation_value), timeout=OVERSHOOT_PROTECTION_STABLE_WAIT)

            return self._calculate_overshoot_value(relative_modulation_value)
        except asyncio.CancelledError as exception:
            await self._coordinator.async_set_heater_state(DeviceState.OFF)
            await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)

            raise exception

    async def _wait_for_flame(self) -> None:
        """Wait until the heating system flame is active."""
        while not self._coordinator.flame_active:
            _LOGGER.warning("Waiting for heating system to start")
            await self._trigger_heating_cycle()

        _LOGGER.info("Heating system has started")

    async def _wait_for_stable_relative_modulation(self) -> float:
        """Wait until the relative modulation stabilizes."""
        previous_average_value = -1

        while True:
            current_value = float(self._coordinator.relative_modulation_value)
            average_value, error_value = self._calculate_exponential_moving_average(previous_average_value, current_value)

            if error_value <= DEADBAND:
                _LOGGER.info("Stable relative modulation reached: %.2f%%", current_value)
                return current_value

            previous_average_value = average_value
            await self._trigger_heating_cycle()
            _LOGGER.debug("Relative Modulation: %s%%, Error: %s%%", current_value, error_value)

    async def _wait_for_stable_temperature(self, relative_modulation_value: float) -> None:
        """Wait until the boiler temperature stabilizes, influenced by relative modulation."""
        starting_temperature = float(self._coordinator.boiler_temperature)
        previous_average_temperature = float(self._coordinator.boiler_temperature)

        while True:
            current_temperature = float(self._coordinator.boiler_temperature)
            average_temperature, error_value = self._calculate_exponential_moving_average(previous_average_temperature, current_temperature)

            if current_temperature > starting_temperature and error_value <= DEADBAND:
                _LOGGER.info("Stable temperature reached: %.2f°C", current_temperature)
                return

            # Adjust heating cycle based on stable relative modulation
            setpoint = (self._setpoint if relative_modulation_value > 0 else current_temperature)
            await self._trigger_heating_cycle(setpoint)

            previous_average_temperature = average_temperature
            _LOGGER.debug("Temperature: %s°C, Error: %s°C", current_temperature, average_temperature)

    def _calculate_overshoot_value(self, relative_modulation_value: float) -> float:
        """Calculate and log the overshoot value."""
        overshoot_value = (100 - relative_modulation_value) / 100 * self._setpoint
        _LOGGER.info("Calculated overshoot value: %.2f", overshoot_value)
        return overshoot_value

    def _calculate_exponential_moving_average(self, previous_average: float, current_value: float) -> tuple[float, float]:
        """Calculate the exponential moving average and error."""
        average_value = self._alpha * current_value + (1 - self._alpha) * previous_average
        error_value = abs(current_value - previous_average)
        return average_value, error_value

    async def _trigger_heating_cycle(self, setpoint: float = None) -> None:
        """Trigger a heating cycle with the coordinator."""
        await self._coordinator.async_set_heater_state(DeviceState.ON)
        await self._coordinator.async_set_control_max_relative_modulation(MAXIMUM_RELATIVE_MOD)
        await self._coordinator.async_set_control_setpoint(setpoint if setpoint is not None else self._setpoint)

        await asyncio.sleep(SLEEP_INTERVAL)
        await self._coordinator.async_control_heating_loop()

    async def _reset_heater_state(self) -> None:
        """Reset the heater state to default settings."""
        await self._coordinator.async_set_heater_state(DeviceState.OFF)
        await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)
