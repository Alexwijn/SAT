import logging
from enum import Enum
from time import monotonic
from typing import Optional, Tuple

from .boiler_state import BoilerState
from .const import HEATER_STARTUP_TIMEFRAME
from .heating_curve import HeatingCurve

_LOGGER = logging.getLogger(__name__)


class PWMState(str, Enum):
    ON = "on"
    OFF = "off"
    IDLE = "idle"


class PWM:
    """Implements Pulse Width Modulation (PWM) control for managing boiler operations."""

    def __init__(self, heating_curve: HeatingCurve, max_cycle_time: int, automatic_duty_cycle: bool, max_cycles: int, force: bool = False):
        """Initialize the PWM control."""
        self._alpha = 0.2
        self._force = force
        self._last_boiler_temperature = None

        self._max_cycles = max_cycles
        self._heating_curve = heating_curve
        self._max_cycle_time = max_cycle_time
        self._automatic_duty_cycle = automatic_duty_cycle

        # Timing thresholds for duty cycle management
        self._on_time_lower_threshold = 180
        self._on_time_upper_threshold = 3600 / self._max_cycles
        self._on_time_max_threshold = self._on_time_upper_threshold * 2

        # Duty cycle percentage thresholds
        self._duty_cycle_lower_threshold = self._on_time_lower_threshold / self._on_time_upper_threshold
        self._duty_cycle_upper_threshold = 1 - self._duty_cycle_lower_threshold
        self._min_duty_cycle_percentage = self._duty_cycle_lower_threshold / 2
        self._max_duty_cycle_percentage = 1 - self._min_duty_cycle_percentage

        self.reset()

    def reset(self) -> None:
        """Reset the PWM control."""
        self._cycles = 0
        self._duty_cycle = None
        self._state = PWMState.IDLE
        self._last_update = monotonic()

        self._first_duty_cycle_start = None
        self._last_duty_cycle_percentage = None

    async def update(self, requested_setpoint: float, boiler: BoilerState) -> None:
        """Update the PWM state based on the output of a PID controller."""
        if not self._heating_curve.value or requested_setpoint is None:
            self._state = PWMState.IDLE
            self._last_update = monotonic()
            self._last_boiler_temperature = boiler.temperature

            reason = "heating curve value" if not self._heating_curve.value else "requested setpoint"
            _LOGGER.warning(f"Turned off PWM due to lack of valid {reason}.")
            return

        if boiler.temperature is not None and self._last_boiler_temperature is None:
            self._last_boiler_temperature = boiler.temperature

        if self._first_duty_cycle_start and (monotonic() - self._first_duty_cycle_start) > 3600:
            self._cycles = 0
            self._first_duty_cycle_start = None
            _LOGGER.debug("Resetting CYCLES to zero, since an hour has passed.")

        elapsed = monotonic() - self._last_update
        self._duty_cycle = self._calculate_duty_cycle(requested_setpoint, boiler)

        _LOGGER.debug("Calculated duty cycle %.0f seconds ON, %.0f seconds OFF, %d CYCLES this hour.", self._duty_cycle[0], self._duty_cycle[1], self._cycles)

        # Update boiler temperature if the heater has just started up
        if self._state == PWMState.ON and boiler.temperature is not None:
            if elapsed <= HEATER_STARTUP_TIMEFRAME:
                self._last_boiler_temperature = self._alpha * boiler.temperature + (1 - self._alpha) * self._last_boiler_temperature
            else:
                self._last_boiler_temperature = boiler.temperature

        # State transitions for PWM
        if self._state != PWMState.ON and self._duty_cycle[0] >= HEATER_STARTUP_TIMEFRAME and (elapsed >= self._duty_cycle[1] or self._state == PWMState.IDLE):
            if self._first_duty_cycle_start is None:
                self._first_duty_cycle_start = monotonic()

            if self._cycles >= self._max_cycles:
                _LOGGER.debug("Preventing duty cycle due to max cycles per hour.")
                return

            self._cycles += 1
            self._state = PWMState.ON
            self._last_update = monotonic()
            self._last_boiler_temperature = boiler.temperature or 0
            _LOGGER.debug("Starting duty cycle.")
            return

        if self._state != PWMState.OFF and (self._duty_cycle[0] < HEATER_STARTUP_TIMEFRAME or elapsed >= self._duty_cycle[0] or self._state == PWMState.IDLE):
            self._state = PWMState.OFF
            self._last_update = monotonic()
            _LOGGER.debug("Finished duty cycle.")
            return

        _LOGGER.debug("Cycle time elapsed %.0f seconds in %s", elapsed, self._state)

    def _calculate_duty_cycle(self, requested_setpoint: float, boiler: BoilerState) -> Tuple[int, int]:
        """Calculate the duty cycle in seconds based on the output of a PID controller and a heating curve value."""
        boiler_temperature = self._last_boiler_temperature or requested_setpoint
        base_offset = self._heating_curve.base_offset

        # Ensure boiler temperature is above the base offset
        boiler_temperature = max(boiler_temperature, base_offset + 1)

        # Calculate duty cycle percentage
        self._last_duty_cycle_percentage = (requested_setpoint - base_offset) / (boiler_temperature - base_offset)
        self._last_duty_cycle_percentage = min(self._last_duty_cycle_percentage, 1)
        self._last_duty_cycle_percentage = max(self._last_duty_cycle_percentage, 0)

        _LOGGER.debug("Requested setpoint %.1f", requested_setpoint)
        _LOGGER.debug("Boiler Temperature %.1f", boiler_temperature)

        _LOGGER.debug("Calculated duty cycle %.2f%%", self._last_duty_cycle_percentage * 100)
        _LOGGER.debug("Calculated duty cycle lower threshold %.2f%%", self._duty_cycle_lower_threshold * 100)
        _LOGGER.debug("Calculated duty cycle upper threshold %.2f%%", self._duty_cycle_upper_threshold * 100)

        # If automatic duty cycle control is disabled
        if not self._automatic_duty_cycle:
            on_time = self._last_duty_cycle_percentage * self._max_cycle_time
            off_time = (1 - self._last_duty_cycle_percentage) * self._max_cycle_time

            return int(on_time), int(off_time)

        # Handle special low-duty cycle cases
        if self._last_duty_cycle_percentage < self._min_duty_cycle_percentage:
            if boiler.flame_active and not boiler.hot_water_active:
                on_time = self._on_time_lower_threshold
                off_time = self._on_time_max_threshold - self._on_time_lower_threshold

                return int(on_time), int(off_time)

            return 0, int(self._on_time_max_threshold)

        # Map duty cycle ranges to on/off times
        if self._last_duty_cycle_percentage <= self._duty_cycle_lower_threshold:
            on_time = self._on_time_lower_threshold
            off_time = (self._on_time_lower_threshold / self._last_duty_cycle_percentage) - self._on_time_lower_threshold

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage <= self._duty_cycle_upper_threshold:
            on_time = self._on_time_upper_threshold * self._last_duty_cycle_percentage
            off_time = self._on_time_upper_threshold * (1 - self._last_duty_cycle_percentage)

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage <= self._max_duty_cycle_percentage:
            on_time = self._on_time_lower_threshold / (1 - self._last_duty_cycle_percentage) - self._on_time_lower_threshold
            off_time = self._on_time_lower_threshold

            return int(on_time), int(off_time)

        # Handle cases where the duty cycle exceeds the maximum allowed percentage
        on_time = self._on_time_max_threshold
        off_time = 0

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
