import logging
from enum import Enum
from time import monotonic
from typing import Optional, Tuple

from custom_components.sat import SatConfigStore
from custom_components.sat.heating_curve import HeatingCurve

_LOGGER = logging.getLogger(__name__)

DUTY_CYCLE_20_PERCENT = 0.2
DUTY_CYCLE_80_PERCENT = 0.8
MIN_DUTY_CYCLE_PERCENTAGE = 0.1
MAX_DUTY_CYCLE_PERCENTAGE = 0.9

ON_TIME_20_PERCENT = 180
ON_TIME_80_PERCENT = 900


class PWMState(Enum):
    ON = "on"
    OFF = "off"
    IDLE = "idle"


class PWM:
    """A class for implementing Pulse Width Modulation (PWM) control."""

    def __init__(self, store: SatConfigStore, heating_curve: HeatingCurve, max_cycle_time: int, automatic_duty_cycle: bool):
        """Initialize the PWM control."""
        self._store = store
        self._heating_curve = heating_curve
        self._max_cycle_time = max_cycle_time
        self._automatic_duty_cycle = automatic_duty_cycle

        self.reset()

    def reset(self) -> None:
        """Reset the PWM control."""
        self._duty_cycle = None
        self._state = PWMState.ON
        self._last_update = monotonic()

    async def update(self, setpoint: float) -> None:
        """Update the PWM state based on the output of a PID controller."""
        if not self._heating_curve.value:
            self._state = PWMState.IDLE
            self._last_update = monotonic()
            _LOGGER.warning("Invalid heating curve value")
            return

        if setpoint is None or setpoint > self._store.retrieve_overshoot_protection_value():
            self._state = PWMState.IDLE
            self._last_update = monotonic()
            _LOGGER.debug("Turned off PWM due exceeding the overshoot protection value")
            return

        elapsed = monotonic() - self._last_update
        self._duty_cycle = self._calculate_duty_cycle(setpoint)

        if self._duty_cycle is None:
            self._state = PWMState.IDLE
            self._last_update = monotonic()
            _LOGGER.debug("Turned off PWM because we are above maximum duty cycle")
            return

        _LOGGER.debug("Calculated duty cycle %.0f seconds ON", self._duty_cycle[0])
        _LOGGER.debug("Calculated duty cycle %.0f seconds OFF", self._duty_cycle[1])

        if self._state != PWMState.ON and self._duty_cycle[0] >= 180 and (elapsed >= self._duty_cycle[1] or self._state == PWMState.IDLE):
            self._state = PWMState.ON
            self._last_update = monotonic()
            _LOGGER.debug("Starting duty cycle.")
            return

        if self._state != PWMState.OFF and (self._duty_cycle[0] < 180 or elapsed >= self._duty_cycle[0] or self._state == PWMState.IDLE):
            self._state = PWMState.OFF
            self._last_update = monotonic()
            _LOGGER.debug("Finished duty cycle.")
            return

        _LOGGER.debug("Cycle time elapsed %.0f seconds", elapsed)

    def _calculate_duty_cycle(self, setpoint: float) -> Optional[Tuple[int, int]]:
        """Calculates the duty cycle in seconds based on the output of a PID controller and a heating curve value."""
        base_offset = self._heating_curve.base_offset
        overshoot_protection = self._store.retrieve_overshoot_protection_value()
        duty_cycle_percentage = (setpoint - base_offset) / (overshoot_protection - base_offset)

        _LOGGER.debug("Requested setpoint %.1f", setpoint)
        _LOGGER.debug("Calculated duty cycle %.0f%%", duty_cycle_percentage * 100)

        if not self._automatic_duty_cycle and duty_cycle_percentage >= 0:
            return int(duty_cycle_percentage * self._max_cycle_time), int((1 - duty_cycle_percentage) * self._max_cycle_time)

        if duty_cycle_percentage < MIN_DUTY_CYCLE_PERCENTAGE:
            return 0, 0

        if duty_cycle_percentage <= DUTY_CYCLE_20_PERCENT:
            on_time = int(ON_TIME_20_PERCENT)
            off_time = int((DUTY_CYCLE_20_PERCENT / duty_cycle_percentage) - DUTY_CYCLE_20_PERCENT)

            return int(on_time), int(off_time)

        if duty_cycle_percentage <= DUTY_CYCLE_80_PERCENT:
            on_time = ON_TIME_80_PERCENT * duty_cycle_percentage
            off_time = ON_TIME_80_PERCENT * (1 - duty_cycle_percentage)

            return int(on_time), int(off_time)

        if duty_cycle_percentage <= MAX_DUTY_CYCLE_PERCENTAGE:
            on_time = ON_TIME_20_PERCENT / (1 - duty_cycle_percentage) - DUTY_CYCLE_20_PERCENT
            off_time = ON_TIME_80_PERCENT - on_time

            return int(on_time), int(off_time)

        if duty_cycle_percentage > MAX_DUTY_CYCLE_PERCENTAGE:
            return None

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
