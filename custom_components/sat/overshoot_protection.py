import asyncio
import logging
import time

from .const import OVERSHOOT_PROTECTION_SETPOINT, MINIMUM_SETPOINT, DEADBAND, MAXIMUM_RELATIVE_MODULATION
from .coordinator import DeviceState, SatDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Constants for timeouts and intervals
OVERSHOOT_PROTECTION_INITIAL_WAIT = 300  # Five minutes in seconds
OVERSHOOT_PROTECTION_STABLE_WAIT = 900  # Fifteen minutes in seconds
OVERSHOOT_PROTECTION_RELATIVE_MODULATION_WAIT = 300  # Five minutes in seconds
SLEEP_INTERVAL = 15  # Sleep interval in seconds


class OvershootProtection:
    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_system: str):
        """Initialize OvershootProtection with a coordinator and heating system configuration."""
        self._alpha: float = 0.5
        self._stable_temperature: float | None = None
        self._coordinator: SatDataUpdateCoordinator = coordinator
        self._setpoint: int = min(OVERSHOOT_PROTECTION_SETPOINT.get(heating_system), coordinator.maximum_setpoint_value)

        if self._setpoint is None:
            raise ValueError(f"Invalid heating system: {heating_system}")

    async def calculate(self) -> float | None:
        """Calculate the overshoot protection value."""
        try:
            _LOGGER.info("Starting overshoot protection calculation")

            # Sequentially ensure the system is ready
            await asyncio.wait_for(self._wait_for_flame(), timeout=OVERSHOOT_PROTECTION_INITIAL_WAIT)

            # Wait for a stable temperature
            await asyncio.wait_for(self._wait_for_stable_temperature(), timeout=OVERSHOOT_PROTECTION_STABLE_WAIT)

            # Wait a bit before calculating the overshoot value, if required
            if self._coordinator.relative_modulation_value > 0:
                await self._wait_a_moment(OVERSHOOT_PROTECTION_RELATIVE_MODULATION_WAIT)

            return self._calculate_overshoot_value()
        except asyncio.CancelledError as exception:
            await self._coordinator.async_set_heater_state(DeviceState.OFF)
            await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)

            raise exception

    async def _wait_for_flame(self) -> None:
        """Wait until the heating system flame is active."""
        while not self._coordinator.flame_active:
            _LOGGER.warning("Waiting for heating system to start")
            await self._trigger_heating_cycle(is_ready=False)

        _LOGGER.info("Heating system has started")

    async def _wait_a_moment(self, wait_time: int) -> None:
        """Wait until the relative modulation stabilizes."""

        start_time = time.time()
        while time.time() - start_time < wait_time:
            await self._trigger_heating_cycle(True)
            await asyncio.sleep(SLEEP_INTERVAL)

    async def _wait_for_stable_temperature(self) -> None:
        """Wait until the boiler temperature stabilizes, influenced by relative modulation."""
        while not self._coordinator.boiler_temperature:
            _LOGGER.warning("Waiting for boiler temperature")

        starting_temperature = self._coordinator.boiler_temperature
        previous_average_temperature = self._coordinator.boiler_temperature

        while True:
            current_temperature = float(self._coordinator.boiler_temperature)
            average_temperature, error_value = self._calculate_exponential_moving_average(previous_average_temperature, current_temperature)

            if current_temperature > starting_temperature and error_value <= DEADBAND:
                self._stable_temperature = current_temperature
                _LOGGER.info("Stable temperature reached: %.2f°C", current_temperature)
                return

            await self._trigger_heating_cycle(is_ready=True)

            previous_average_temperature = average_temperature
            _LOGGER.warning("Waiting for a stable temperature")
            _LOGGER.debug("Temperature: %s°C, Error: %s°C", current_temperature, error_value)

    def _calculate_overshoot_value(self) -> float:
        """Calculate and log the overshoot value."""
        if self._coordinator.relative_modulation_value == 0:
            return self._stable_temperature

        return (100 - self._coordinator.relative_modulation_value) / 100 * self._setpoint

    def _calculate_exponential_moving_average(self, previous_average: float, current_value: float) -> tuple[float, float]:
        """Calculate the exponential moving average and error."""
        average_value = self._alpha * current_value + (1 - self._alpha) * previous_average
        error_value = abs(current_value - previous_average)
        return average_value, error_value

    async def _trigger_heating_cycle(self, is_ready: bool) -> None:
        """Trigger a heating cycle with the coordinator."""
        await self._coordinator.async_set_heater_state(DeviceState.ON)
        await self._coordinator.async_set_control_setpoint(await self._get_setpoint(is_ready))
        await self._coordinator.async_set_control_max_relative_modulation(MAXIMUM_RELATIVE_MODULATION)

        await asyncio.sleep(SLEEP_INTERVAL)
        await self._coordinator.async_control_heating_loop()

    async def _get_setpoint(self, is_ready: bool) -> float:
        """Get the setpoint for the heating cycle."""
        return self._setpoint if not is_ready or self._coordinator.relative_modulation_value > 0 else self._coordinator.boiler_temperature

    async def _reset_heater_state(self) -> None:
        """Reset the heater state to default settings."""
        await self._coordinator.async_set_heater_state(DeviceState.OFF)
        await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)
