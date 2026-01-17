import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from .const import *
from .coordinator import SatDataUpdateCoordinator
from .helpers import timestamp, seconds_since
from .types import HeaterState

_LOGGER = logging.getLogger(__name__)

# Constants for timeouts and intervals
OVERSHOOT_PROTECTION_INITIAL_WAIT = 300  # Five minutes in seconds
OVERSHOOT_PROTECTION_STABLE_WAIT = 900  # Fifteen minutes in seconds
OVERSHOOT_PROTECTION_RELATIVE_MODULATION_WAIT = 300  # Five minutes in seconds

SLEEP_INTERVAL_SECONDS = 15  # Sleep interval in seconds
MINIMUM_WARMUP_RISE = 0.5  # Minimum rise from starting temperature (°C)

STABILITY_WINDOW_DURATION_SECONDS = 300  # Five minutes window for stability
STABILITY_MINIMUM_DURATION_SECONDS = 180  # Minimum window duration
STABILITY_MINIMUM_SAMPLES = 6  # Minimum number of samples
STABILITY_TEMPERATURE_RANGE = 0.3  # Max temperature range in window (°C)
STABILITY_SLOPE_CELSIUS_PER_SECOND = 0.0005  # Max slope (°C/s)


@dataclass
class OvershootProtectionSampleStatistics:
    duration: float
    slope: float
    temperature_range: float
    average_temperature: float
    average_modulation: Optional[float]


class OvershootProtection:
    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_system: str):
        """Initialize OvershootProtection with a coordinator and heating system configuration."""
        if heating_system not in OVERSHOOT_PROTECTION_SETPOINT:
            raise ValueError(f"Invalid heating system: {heating_system}")

        self._stable_temperature: Optional[float] = None
        self._stable_modulation: Optional[float] = None
        self._coordinator: SatDataUpdateCoordinator = coordinator
        self._samples: list[tuple[float, float, Optional[float]]] = []

        maximum_setpoint = coordinator.maximum_setpoint_value
        default_setpoint = float(OVERSHOOT_PROTECTION_SETPOINT.get(heating_system))

        if maximum_setpoint is None:
            self._setpoint = default_setpoint
        else:
            self._setpoint = float(min(default_setpoint, maximum_setpoint))

    async def calculate(self) -> Optional[float]:
        """Calculate the overshoot protection value."""
        try:
            _LOGGER.info("Starting overshoot protection calculation")

            # Sequentially ensure the system is ready
            await asyncio.wait_for(self._wait_for_flame(), timeout=OVERSHOOT_PROTECTION_INITIAL_WAIT)

            # Wait for a stable temperature
            await asyncio.wait_for(self._wait_for_stable_temperature(), timeout=OVERSHOOT_PROTECTION_STABLE_WAIT)

            # Wait a bit before calculating the overshoot value, if required
            if (self._coordinator.relative_modulation_value or 0) > 0:
                await self._wait_a_moment(OVERSHOOT_PROTECTION_RELATIVE_MODULATION_WAIT)

            return self._calculate_overshoot_value()
        except (asyncio.CancelledError, Exception):
            await self._reset_heater_state()
            raise

    async def _wait_for_flame(self) -> None:
        """Wait until the heating system flame is active."""
        while not self._coordinator.flame_active:
            _LOGGER.warning("Waiting for heating system to start")
            await self._trigger_heating_cycle(is_ready=False)

        _LOGGER.info("Heating system has started")

    async def _wait_a_moment(self, wait_time: int) -> None:
        """Wait until the relative modulation stabilizes."""

        start_time = timestamp()
        while seconds_since(start_time) < wait_time:
            await self._trigger_heating_cycle(True)
            self._maybe_record_sample()
            await asyncio.sleep(SLEEP_INTERVAL_SECONDS)

    async def _wait_for_stable_temperature(self) -> None:
        """Wait until the boiler temperature stabilizes, influenced by relative modulation."""
        while not self._coordinator.boiler_temperature:
            _LOGGER.warning("Waiting for boiler temperature")
            await self._trigger_heating_cycle(is_ready=False)

        starting_temperature = self._coordinator.boiler_temperature

        while True:
            current_temperature = float(self._coordinator.boiler_temperature)
            self._record_sample(timestamp(), current_temperature, self._coordinator.relative_modulation_value)
            if (sample_statistics := self._sample_statistics()) is not None and self._is_stable(sample_statistics, starting_temperature):
                self._stable_temperature = sample_statistics.average_temperature
                self._stable_modulation = sample_statistics.average_modulation
                _LOGGER.info("Stable temperature reached: %.2f°C", self._stable_temperature)
                return

            await self._trigger_heating_cycle(is_ready=True)
            _LOGGER.warning("Waiting for a stable temperature")
            _LOGGER.debug("Temperature: %s°C", current_temperature)

    def _calculate_overshoot_value(self) -> float:
        """Calculate and log the overshoot value."""
        modulation_value = self._stable_modulation if self._stable_modulation is not None else self._coordinator.relative_modulation_value
        if not modulation_value or modulation_value == 0:
            return self._stable_temperature

        return (100 - float(modulation_value)) / 100 * self._setpoint

    async def _trigger_heating_cycle(self, is_ready: bool) -> None:
        """Trigger a heating cycle with the coordinator."""
        await self._coordinator.async_set_heater_state(HeaterState.ON)
        await self._coordinator.async_set_control_setpoint(await self._get_setpoint(is_ready))
        await self._coordinator.async_set_control_max_relative_modulation(MAXIMUM_RELATIVE_MODULATION)

        await asyncio.sleep(SLEEP_INTERVAL_SECONDS)
        await self._coordinator.async_control_heating_loop()

    async def _get_setpoint(self, is_ready: bool) -> float:
        """Get the setpoint for the heating cycle."""
        if (boiler_temperature := self._coordinator.boiler_temperature) is None:
            return self._setpoint

        return self._setpoint if not is_ready or self._coordinator.relative_modulation_value > 0 else boiler_temperature

    async def _reset_heater_state(self) -> None:
        """Reset the heater state to default settings."""
        await self._coordinator.async_set_heater_state(HeaterState.OFF)
        await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)

    def _record_sample(self, time_value: float, temperature: float, modulation: Optional[float]) -> None:
        """Record a sample and keep only the recent window."""
        self._samples.append((time_value, temperature, modulation))
        cutoff = time_value - STABILITY_WINDOW_DURATION_SECONDS
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)

    def _maybe_record_sample(self) -> None:
        """Record a sample if boiler temperature is available."""
        if (temperature := self._coordinator.boiler_temperature) is None:
            return

        self._record_sample(timestamp(), float(temperature), self._coordinator.relative_modulation_value)

    def _sample_statistics(self) -> Optional[OvershootProtectionSampleStatistics]:
        """Compute sample statistics for the current sample window."""
        if len(self._samples) < STABILITY_MINIMUM_SAMPLES:
            return None

        sample_times = [sample[0] for sample in self._samples]
        sample_temperatures = [sample[1] for sample in self._samples]

        duration = sample_times[-1] - sample_times[0]
        if duration <= 0:
            return None

        slope = (sample_temperatures[-1] - sample_temperatures[0]) / duration
        temperature_range = max(sample_temperatures) - min(sample_temperatures)
        modulations = [sample[2] for sample in self._samples if sample[2] is not None]

        average_temperature = sum(sample_temperatures) / float(len(sample_temperatures))
        average_modulation = sum(modulations) / float(len(modulations)) if modulations else None

        return OvershootProtectionSampleStatistics(
            slope=slope,
            duration=duration,
            temperature_range=temperature_range,
            average_modulation=average_modulation,
            average_temperature=average_temperature,
        )

    @staticmethod
    def _is_stable(sample_statistics: OvershootProtectionSampleStatistics, starting_temperature: float) -> bool:
        """Return True when the window indicates stable temperature."""
        if sample_statistics.duration < STABILITY_MINIMUM_DURATION_SECONDS:
            return False

        if sample_statistics.average_temperature < starting_temperature + MINIMUM_WARMUP_RISE:
            return False

        if sample_statistics.temperature_range > STABILITY_TEMPERATURE_RANGE:
            return False

        return abs(sample_statistics.slope) <= STABILITY_SLOPE_CELSIUS_PER_SECOND
