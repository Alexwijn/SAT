import logging
from dataclasses import dataclass
from time import monotonic
from typing import Optional, Tuple, TYPE_CHECKING

from homeassistant.core import State

from .const import HEATER_STARTUP_TIMEFRAME, PWMStatus
from .heating_curve import HeatingCurve

if TYPE_CHECKING:
    from .boiler import BoilerState

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class PWMState:
    """
    Encapsulates the state of the PWM control.
    """
    enabled: bool
    status: PWMStatus
    duty_cycle: Optional[Tuple[int, int]]
    last_duty_cycle_percentage: Optional[float]


@dataclass(frozen=True, slots=True, kw_only=True)
class PWMConfig:
    """
    Encapsulates settings related to cycle time and maximum cycles.
    """
    maximum_cycles: int
    maximum_cycle_time: int


class PWM:
    """Implements Pulse Width Modulation (PWM) control for managing boiler operations."""

    def __init__(self, config: PWMConfig, heating_curve: HeatingCurve, automatic_duty_cycle: bool):
        """Initialize the PWM control."""
        self._alpha: float = 0.2
        self._last_boiler_temperature: float | None = None

        self._config: PWMConfig = config
        self._heating_curve: HeatingCurve = heating_curve
        self._automatic_duty_cycle: bool = automatic_duty_cycle

        # Timing thresholds for duty cycle management
        self._on_time_lower_threshold: float = 180
        self._on_time_upper_threshold: float = 3600 / max(1, self._config.maximum_cycles)
        self._on_time_maximum_threshold: float = self._on_time_upper_threshold * 2

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
        self._enabled = False
        self._current_cycle: int = 0
        self._status: PWMStatus = PWMStatus.IDLE
        self._last_update: float = monotonic()
        self._duty_cycle: Tuple[int, int] | None = None

        self._first_duty_cycle_start: float | None = None
        self._last_duty_cycle_percentage: float | None = None

    def restore(self, state: State) -> None:
        """Restore the PWM controller from a saved state."""
        if enabled := state.attributes.get("pulse_width_modulation_enabled"):
            self._enabled = bool(enabled)
            _LOGGER.debug("Restored Pulse Width Modulation state: %s", enabled)

    def enable(self, boiler_state: "BoilerState", requested_setpoint: Optional[float]) -> None:
        """Enable and the PWM state based on the output of a PID controller."""
        self._enabled = True

        if not self._heating_curve.value or requested_setpoint is None or boiler_state.flow_temperature is None:
            self._status = PWMStatus.IDLE
            self._last_update = monotonic()
            self._last_boiler_temperature = boiler_state.flow_temperature

            _LOGGER.warning("PWM turned off due missing values.")

            return

        if self._last_boiler_temperature is None:
            self._last_boiler_temperature = boiler_state.flow_temperature
            _LOGGER.debug("Initialized last boiler temperature to %.1f°C", boiler_state.flow_temperature)

        if self._first_duty_cycle_start is None or (monotonic() - self._first_duty_cycle_start) > 3600:
            self._current_cycle = 0
            self._first_duty_cycle_start = monotonic()
            _LOGGER.info("CYCLES count reset for the rolling hour.")

        elapsed = monotonic() - self._last_update
        self._duty_cycle = self._calculate_duty_cycle(requested_setpoint, boiler_state)

        # Update boiler temperature if heater has just started up
        if self._status == PWMStatus.ON:
            if elapsed <= HEATER_STARTUP_TIMEFRAME:
                self._last_boiler_temperature = (self._alpha * boiler_state.flow_temperature + (1 - self._alpha) * self._last_boiler_temperature)

                _LOGGER.debug("Updated last boiler temperature with weighted average during startup phase to %.1f°C", self._last_boiler_temperature)
            else:
                self._last_boiler_temperature = boiler_state.flow_temperature
                _LOGGER.debug("Updated last boiler temperature to %.1f°C", self._last_boiler_temperature)

        # State transitions for PWM
        if self._status != PWMStatus.ON and self._duty_cycle[0] >= HEATER_STARTUP_TIMEFRAME and (elapsed >= self._duty_cycle[1] or self._status == PWMStatus.IDLE):
            if self._current_cycle >= self._config.maximum_cycles:
                _LOGGER.info("Reached max cycles per hour, preventing new duty cycle.")
                return

            self._current_cycle += 1
            self._status = PWMStatus.ON
            self._last_update = monotonic()
            self._last_boiler_temperature = boiler_state.flow_temperature
            _LOGGER.info("Starting new duty cycle (ON state). Current CYCLES count: %d", self._current_cycle)
            return

        if self._status != PWMStatus.OFF and (self._duty_cycle[0] < HEATER_STARTUP_TIMEFRAME or elapsed >= self._duty_cycle[0] or self._status == PWMStatus.IDLE):
            self._status = PWMStatus.OFF
            self._last_update = monotonic()
            _LOGGER.info("Duty cycle completed. Switching to OFF state.")
            return

        _LOGGER.debug("Cycle time elapsed: %.0f seconds in state: %s", elapsed, self._status)

    def disable(self) -> None:
        """Disable the PWM control."""
        self.reset()
        self._enabled = False

    def _calculate_duty_cycle(self, requested_setpoint: float, boiler: "BoilerState") -> Tuple[int, int]:
        """Calculate the duty cycle in seconds based on the output of a PID controller and a heating curve value."""
        base_offset = self._heating_curve.base_offset
        boiler_temperature = self._last_boiler_temperature

        # Ensure the boiler temperature is above the base offset
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
            on_time = self._last_duty_cycle_percentage * self._config.maximum_cycle_time
            off_time = (1 - self._last_duty_cycle_percentage) * self._config.maximum_cycle_time

            _LOGGER.debug(
                "Calculated on_time: %.0f seconds, off_time: %.0f seconds.",
                on_time, off_time
            )

            return int(on_time), int(off_time)

        # Handle special low-duty cycle cases
        if self._last_duty_cycle_percentage < self._min_duty_cycle_percentage:
            if boiler.flame_active and not boiler.hot_water_active:
                on_time = self._on_time_lower_threshold
                off_time = self._on_time_maximum_threshold - self._on_time_lower_threshold

                _LOGGER.debug(
                    "Special low-duty case with flame active. Setting on_time: %d seconds, off_time: %d seconds.",
                    on_time, off_time
                )

                return int(on_time), int(off_time)

            _LOGGER.debug("Special low-duty case without flame. Setting on_time: 0 seconds, off_time: %d seconds.", self._on_time_maximum_threshold)
            return 0, int(self._on_time_maximum_threshold)

        # Mapping duty cycle ranges to on/off times
        if self._last_duty_cycle_percentage <= self._duty_cycle_lower_threshold:
            on_time = self._on_time_lower_threshold
            off_time = (self._on_time_lower_threshold / self._last_duty_cycle_percentage) - self._on_time_lower_threshold

            _LOGGER.debug(
                "Low duty cycle range, cycles this hour: %d. Calculated on_time: %d seconds, off_time: %d seconds.",
                self._current_cycle, on_time, off_time
            )

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage <= self._duty_cycle_upper_threshold:
            on_time = self._on_time_upper_threshold * self._last_duty_cycle_percentage
            off_time = self._on_time_upper_threshold * (1 - self._last_duty_cycle_percentage)

            _LOGGER.debug(
                "Mid-range duty cycle, cycles this hour: %d. Calculated on_time: %d seconds, off_time: %d seconds.",
                self._current_cycle, on_time, off_time
            )

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage <= self._max_duty_cycle_percentage:
            on_time = self._on_time_lower_threshold / (1 - self._last_duty_cycle_percentage) - self._on_time_lower_threshold
            off_time = self._on_time_lower_threshold

            _LOGGER.debug(
                "High duty cycle range, cycles this hour: %d. Calculated on_time: %d seconds, off_time: %d seconds.",
                self._current_cycle, on_time, off_time
            )

            return int(on_time), int(off_time)

        # Handle cases where the duty cycle exceeds the maximum allowed percentage
        on_time = self._on_time_maximum_threshold
        off_time = 0

        _LOGGER.debug("Maximum duty cycle exceeded. Setting on_time: %d seconds, off_time: %d seconds.", on_time, off_time)
        return int(on_time), int(off_time)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def status(self) -> PWMStatus:
        return self._status

    @property
    def state(self):
        return PWMState(
            status=self._status,
            enabled=self._enabled,
            duty_cycle=self._duty_cycle,
            last_duty_cycle_percentage=round(self._last_duty_cycle_percentage * 100, 2) if self._last_duty_cycle_percentage is not None else None
        )
