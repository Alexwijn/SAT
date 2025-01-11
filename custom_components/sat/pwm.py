import logging
from enum import Enum
from time import monotonic
from typing import Optional, Tuple

from .boiler import BoilerState
from .const import HEATER_STARTUP_TIMEFRAME
from .heating_curve import HeatingCurve

_LOGGER = logging.getLogger(__name__)


class PWMState(str, Enum):
    """The current state of Pulse Width Modulation"""
    ON = "on"
    OFF = "off"
    IDLE = "idle"


class PWM:
    """Implements Pulse Width Modulation (PWM) control for managing boiler operations."""

    def __init__(self, heating_curve: HeatingCurve, max_cycle_time: int, automatic_duty_cycle: bool, max_cycles: int, force: bool = False):
        """Initialize the PWM control."""
        self._alpha: float = 0.2
        self._force: bool = force
        self._last_boiler_temperature: float | None = None

        self._max_cycles: int = max_cycles
        self._heating_curve: HeatingCurve = heating_curve
        self._max_cycle_time: int = max_cycle_time
        self._automatic_duty_cycle: bool = automatic_duty_cycle

        # Timing thresholds for duty cycle management
        self._on_time_lower_threshold: float = 180
        self._on_time_upper_threshold: float = 3600 / self._max_cycles
        self._on_time_max_threshold: float = self._on_time_upper_threshold * 2

        # Duty cycle percentage thresholds
        self._duty_cycle_lower_threshold: float = self._on_time_lower_threshold / self._on_time_upper_threshold
        self._duty_cycle_upper_threshold: float = 1 - self._duty_cycle_lower_threshold
        self._min_duty_cycle_percentage: float = self._duty_cycle_lower_threshold / 2
        self._max_duty_cycle_percentage: float = 1 - self._min_duty_cycle_percentage

        _LOGGER.debug(
            "Initialized PWM control with duty cycle thresholds - Lower: %.2f%%, Upper: %.2f%%",
            self._duty_cycle_lower_threshold * 100, self._duty_cycle_upper_threshold * 100
        )

        self.reset()

    def reset(self) -> None:
        """Reset the PWM control."""
        self._cycles: int = 0
        self._state: PWMState = PWMState.IDLE
        self._last_update: float = monotonic()
        self._duty_cycle: Tuple[int, int] | None = None

        self._first_duty_cycle_start: float | None = None
        self._last_duty_cycle_percentage: float | None = None

        _LOGGER.info("PWM control reset to initial state.")

    async def update(self, requested_setpoint: float, boiler: BoilerState) -> None:
        """Update the PWM state based on the output of a PID controller."""
        if not self._heating_curve.value or requested_setpoint is None or boiler.temperature is None:
            self._state = PWMState.IDLE
            self._last_update = monotonic()
            self._last_boiler_temperature = boiler.temperature

            _LOGGER.warning("PWM turned off due missing values.")

            return

        if self._last_boiler_temperature is None:
            self._last_boiler_temperature = boiler.temperature
            _LOGGER.debug("Initialized last boiler temperature to %.1f°C", boiler.temperature)

        if self._first_duty_cycle_start is None or (monotonic() - self._first_duty_cycle_start) > 3600:
            self._cycles = 0
            self._first_duty_cycle_start = monotonic()
            _LOGGER.info("CYCLES count reset for the rolling hour.")

        elapsed = monotonic() - self._last_update
        self._duty_cycle = self._calculate_duty_cycle(requested_setpoint, boiler)

        # Update boiler temperature if heater has just started up
        if self._state == PWMState.ON:
            if elapsed <= HEATER_STARTUP_TIMEFRAME:
                self._last_boiler_temperature = (self._alpha * boiler.temperature + (1 - self._alpha) * self._last_boiler_temperature)

                _LOGGER.debug("Updated last boiler temperature with weighted average during startup phase.")
            else:
                self._last_boiler_temperature = boiler.temperature
                _LOGGER.debug("Updated last boiler temperature to %.1f°C", boiler.temperature)

        # State transitions for PWM
        if self._state != PWMState.ON and self._duty_cycle[0] >= HEATER_STARTUP_TIMEFRAME and (elapsed >= self._duty_cycle[1] or self._state == PWMState.IDLE):
            if self._cycles >= self._max_cycles:
                _LOGGER.info("Reached max cycles per hour, preventing new duty cycle.")
                return

            self._cycles += 1
            self._state = PWMState.ON
            self._last_update = monotonic()
            self._last_boiler_temperature = boiler.temperature
            _LOGGER.info("Starting new duty cycle (ON state). Current CYCLES count: %d", self._cycles)
            return

        if self._state != PWMState.OFF and (self._duty_cycle[0] < HEATER_STARTUP_TIMEFRAME or elapsed >= self._duty_cycle[0] or self._state == PWMState.IDLE):
            self._state = PWMState.OFF
            self._last_update = monotonic()
            _LOGGER.info("Duty cycle completed. Switching to OFF state.")
            return

        _LOGGER.debug("Cycle time elapsed: %.0f seconds in state: %s", elapsed, self._state)

    def _calculate_duty_cycle(self, requested_setpoint: float, boiler: BoilerState) -> Tuple[int, int]:
        """Calculate the duty cycle in seconds based on the output of a PID controller and a heating curve value."""
        boiler_temperature = self._last_boiler_temperature
        base_offset = self._heating_curve.base_offset

        # Ensure boiler temperature is above the base offset
        boiler_temperature = max(boiler_temperature, base_offset + 1)

        # Calculate duty cycle percentage
        self._last_duty_cycle_percentage = (requested_setpoint - base_offset) / (boiler_temperature - base_offset)
        self._last_duty_cycle_percentage = min(max(self._last_duty_cycle_percentage, 0), 1)

        _LOGGER.debug(
            "Duty cycle calculation - Requested setpoint: %.1f°C, Boiler temperature: %.1f°C, Duty cycle percentage: %.2f%%",
            requested_setpoint, boiler_temperature, self._last_duty_cycle_percentage * 100
        )

        # If automatic duty cycle control is disabled
        if not self._automatic_duty_cycle:
            on_time = self._last_duty_cycle_percentage * self._max_cycle_time
            off_time = (1 - self._last_duty_cycle_percentage) * self._max_cycle_time

            _LOGGER.debug(
                "Calculated on_time: %.0f seconds, off_time: %.0f seconds.",
                on_time, off_time
            )

            return int(on_time), int(off_time)

        # Handle special low-duty cycle cases
        if self._last_duty_cycle_percentage < self._min_duty_cycle_percentage:
            if boiler.flame_active and not boiler.hot_water_active:
                on_time = self._on_time_lower_threshold
                off_time = self._on_time_max_threshold - self._on_time_lower_threshold

                _LOGGER.debug(
                    "Special low-duty case with flame active. Setting on_time: %d seconds, off_time: %d seconds.",
                    on_time, off_time
                )

                return int(on_time), int(off_time)

            _LOGGER.debug("Special low-duty case without flame. Setting on_time: 0 seconds, off_time: %d seconds.", self._on_time_max_threshold)
            return 0, int(self._on_time_max_threshold)

        # Mapping duty cycle ranges to on/off times
        if self._last_duty_cycle_percentage <= self._duty_cycle_lower_threshold:
            on_time = self._on_time_lower_threshold
            off_time = (self._on_time_lower_threshold / self._last_duty_cycle_percentage) - self._on_time_lower_threshold

            _LOGGER.debug(
                "Low duty cycle range, cycles this hour: %d. Calculated on_time: %d seconds, off_time: %d seconds.",
                self._cycles, on_time, off_time
            )

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage <= self._duty_cycle_upper_threshold:
            on_time = self._on_time_upper_threshold * self._last_duty_cycle_percentage
            off_time = self._on_time_upper_threshold * (1 - self._last_duty_cycle_percentage)

            _LOGGER.debug(
                "Mid-range duty cycle, cycles this hour: %d. Calculated on_time: %d seconds, off_time: %d seconds.",
                self._cycles, on_time, off_time
            )

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage <= self._max_duty_cycle_percentage:
            on_time = self._on_time_lower_threshold / (1 - self._last_duty_cycle_percentage) - self._on_time_lower_threshold
            off_time = self._on_time_lower_threshold

            _LOGGER.debug(
                "High duty cycle range, cycles this hour: %d. Calculated on_time: %d seconds, off_time: %d seconds.",
                self._cycles, on_time, off_time
            )

            return int(on_time), int(off_time)

        # Handle cases where the duty cycle exceeds the maximum allowed percentage
        on_time = self._on_time_max_threshold
        off_time = 0

        _LOGGER.debug("Maximum duty cycle exceeded. Setting on_time: %d seconds, off_time: %d seconds.", on_time, off_time)
        return int(on_time), int(off_time)

    @property
    def state(self) -> PWMState:
        """Current PWM state."""
        return self._state

    @property
    def duty_cycle(self) -> Optional[Tuple[int, int]]:
        """Current duty cycle as a tuple of (on_time, off_time) in seconds, or None if inactive."""
        return self._duty_cycle

    @property
    def last_duty_cycle_percentage(self) -> Optional[float]:
        """Returns the last calculated duty cycle percentage."""
        return round(self._last_duty_cycle_percentage * 100, 2) if self._last_duty_cycle_percentage is not None else None
