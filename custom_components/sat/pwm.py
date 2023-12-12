import logging
from enum import Enum
from time import monotonic
from typing import Optional, Tuple

from .const import HEATER_STARTUP_TIMEFRAME
from .heating_curve import HeatingCurve

_LOGGER = logging.getLogger(__name__)

DUTY_CYCLE_20_PERCENT = 0.2
DUTY_CYCLE_80_PERCENT = 0.8
MIN_DUTY_CYCLE_PERCENTAGE = 0.1
MAX_DUTY_CYCLE_PERCENTAGE = 0.9

ON_TIME_20_PERCENT = 180
ON_TIME_80_PERCENT = 900


class PWMState(str, Enum):
    ON = "on"
    OFF = "off"
    IDLE = "idle"


class PWM:
    """A class for implementing Pulse Width Modulation (PWM) control."""

    def __init__(self, heating_curve: HeatingCurve, max_cycle_time: int, automatic_duty_cycle: bool, force: bool = False):
        """Initialize the PWM control."""
        self._alpha = 0.2
        self._force = force
        self._last_boiler_temperature = None
        self._last_duty_cycle_percentage = None

        self._heating_curve = heating_curve
        self._max_cycle_time = max_cycle_time
        self._automatic_duty_cycle = automatic_duty_cycle

        self.reset()

    def reset(self) -> None:
        """Reset the PWM control."""
        self._duty_cycle = None
        self._state = PWMState.IDLE
        self._last_update = monotonic()

    async def update(self, requested_setpoint: float, boiler_temperature: float) -> None:
        """Update the PWM state based on the output of a PID controller."""
        if not self._heating_curve.value:
            self._state = PWMState.IDLE
            self._last_update = monotonic()
            _LOGGER.warning("Turned off PWM due since we do not have a valid heating curve value.")
            return

        if requested_setpoint is None:
            self._state = PWMState.IDLE
            self._last_update = monotonic()
            self._last_boiler_temperature = boiler_temperature
            _LOGGER.debug("Turned off PWM due since we do not have a valid requested setpoint.")
            return

        if boiler_temperature is not None and self._last_boiler_temperature is None:
            self._last_boiler_temperature = boiler_temperature

        elapsed = monotonic() - self._last_update
        self._duty_cycle = self._calculate_duty_cycle(requested_setpoint)

        _LOGGER.debug("Calculated duty cycle %.0f seconds ON", self._duty_cycle[0])
        _LOGGER.debug("Calculated duty cycle %.0f seconds OFF", self._duty_cycle[1])

        if self._state == PWMState.ON and boiler_temperature is not None:
            if elapsed <= HEATER_STARTUP_TIMEFRAME:
                self._last_boiler_temperature = self._alpha * boiler_temperature + (1 - self._alpha) * self._last_boiler_temperature
            else:
                self._last_boiler_temperature = boiler_temperature

        if self._state != PWMState.ON and self._duty_cycle[0] >= HEATER_STARTUP_TIMEFRAME and (elapsed >= self._duty_cycle[1] or self._state == PWMState.IDLE):
            self._state = PWMState.ON
            self._last_update = monotonic()
            self._last_boiler_temperature = boiler_temperature or 0
            _LOGGER.debug("Starting duty cycle.")
            return

        if self._state != PWMState.OFF and (self._duty_cycle[0] < HEATER_STARTUP_TIMEFRAME or elapsed >= self._duty_cycle[0] or self._state == PWMState.IDLE):
            self._state = PWMState.OFF
            self._last_update = monotonic()
            _LOGGER.debug("Finished duty cycle.")
            return

        _LOGGER.debug("Cycle time elapsed %.0f seconds in %s", elapsed, self._state)

    def _calculate_duty_cycle(self, requested_setpoint: float) -> Optional[Tuple[int, int]]:
        """Calculates the duty cycle in seconds based on the output of a PID controller and a heating curve value."""
        boiler_temperature = self._last_boiler_temperature or requested_setpoint
        base_offset = self._heating_curve.base_offset

        if boiler_temperature < base_offset:
            boiler_temperature = base_offset + 1

        self._last_duty_cycle_percentage = (requested_setpoint - base_offset) / (boiler_temperature - base_offset)
        self._last_duty_cycle_percentage = min(self._last_duty_cycle_percentage, 1)
        self._last_duty_cycle_percentage = max(self._last_duty_cycle_percentage, 0)

        _LOGGER.debug("Requested setpoint %.1f", requested_setpoint)
        _LOGGER.debug("Boiler Temperature %.1f", boiler_temperature)
        _LOGGER.debug("Calculated duty cycle %.2f%%", self._last_duty_cycle_percentage * 100)

        if not self._automatic_duty_cycle:
            return int(self._last_duty_cycle_percentage * self._max_cycle_time), int((1 - self._last_duty_cycle_percentage) * self._max_cycle_time)

        if self._last_duty_cycle_percentage < MIN_DUTY_CYCLE_PERCENTAGE:
            return 0, 1800

        if self._last_duty_cycle_percentage <= DUTY_CYCLE_20_PERCENT:
            on_time = ON_TIME_20_PERCENT
            off_time = (ON_TIME_20_PERCENT / self._last_duty_cycle_percentage) - ON_TIME_20_PERCENT

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage <= DUTY_CYCLE_80_PERCENT:
            on_time = ON_TIME_80_PERCENT * self._last_duty_cycle_percentage
            off_time = ON_TIME_80_PERCENT * (1 - self._last_duty_cycle_percentage)

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage <= MAX_DUTY_CYCLE_PERCENTAGE:
            on_time = ON_TIME_20_PERCENT / (1 - self._last_duty_cycle_percentage) - ON_TIME_20_PERCENT
            off_time = ON_TIME_20_PERCENT

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage > MAX_DUTY_CYCLE_PERCENTAGE:
            return 1800, 0

    @property
    def state(self) -> PWMState:
        """Returns the current state of the PWM control."""
        return self._state

    @property
    def duty_cycle(self) -> None | tuple[int, int]:
        """
        Returns the current duty cycle of the PWM control.

        If the PWM control is not currently active, None is returned.
        Otherwise, a tuple is returned with the on and off times of the duty cycle in seconds.
        """
        return self._duty_cycle

    @property
    def last_duty_cycle_percentage(self):
        return round(self._last_duty_cycle_percentage * 100, 2)
