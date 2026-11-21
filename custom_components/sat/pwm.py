import logging
from dataclasses import dataclass
from typing import Optional, Tuple, TYPE_CHECKING

from homeassistant.core import State

from .const import HEATER_STARTUP_TIMEFRAME, PWM_IGNITION_TIMEOUT_SECONDS
from .device import DeviceState
from .entry_data import PwmConfig
from .helpers import timestamp
from .types import CycleClassification, CycleControlMode, HeatingSystem, PWMStatus

if TYPE_CHECKING:
    from .cycles import Cycle

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class PWMState:
    """ Encapsulates the state of the PWM control. """
    enabled: bool
    status: PWMStatus
    duty_cycle: Optional[Tuple[int, int]]
    last_duty_cycle_percentage: Optional[float]
    last_switched_at: Optional[float] = None
    effective_on_temperature: Optional[float] = None
    floor_observed: bool = False

    @property
    def floor_known(self) -> bool:
        return self.effective_on_temperature is not None

    @property
    def on_time_seconds(self) -> Optional[int]:
        if self.duty_cycle is None:
            return None

        return self.duty_cycle[0]

    @property
    def off_time_seconds(self) -> Optional[int]:
        if self.duty_cycle is None:
            return None

        return self.duty_cycle[1]


class PWM:
    """Implements Pulse Width Modulation (PWM) control for managing boiler operations."""

    def __init__(self, config: PwmConfig, heating_system: HeatingSystem):
        """Initialize the PWM control."""
        self._config: PwmConfig = config
        self._heating_system: HeatingSystem = heating_system

        # Timing thresholds for duty cycle management
        self._on_time_lower_threshold: float = 180
        self._on_time_upper_threshold: float = 3600 / max(1, self._config.cycles_per_hour)
        self._on_time_maximum_threshold: float = self._on_time_upper_threshold * 2

        # Duty cycle percentage thresholds
        self._duty_cycle_lower_threshold: float = self._on_time_lower_threshold / self._on_time_upper_threshold
        self._duty_cycle_upper_threshold: float = 1 - self._duty_cycle_lower_threshold
        self._min_duty_cycle_percentage: float = self._duty_cycle_lower_threshold / 2
        self._max_duty_cycle_percentage: float = 1 - self._min_duty_cycle_percentage

        self._enabled: bool = False
        self._effective_on_temperature: Optional[float] = None
        self.reset()

        _LOGGER.debug(
            "Initialized PWM control with duty cycle thresholds - Lower: %.2f%%, Upper: %.2f%%",
            self._duty_cycle_lower_threshold * 100, self._duty_cycle_upper_threshold * 100
        )

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
            effective_on_temperature=self._effective_on_temperature,
            floor_observed=self._floor_observed_at is not None,
            last_switched_at=self._last_update if self._status != PWMStatus.IDLE else None,
            last_duty_cycle_percentage=round(self._last_duty_cycle_percentage * 100, 2) if self._last_duty_cycle_percentage is not None else None
        )

    @property
    def maximum_cycle_seconds(self) -> float:
        """Return the longest cycle the duty cycle calculation can produce."""
        return self._on_time_maximum_threshold

    def reset(self) -> None:
        """Reset the PWM cycle state while keeping the learned enablement and boiler floor."""
        self._clear_phase()
        self._cycle_starts: list[float] = []

    def _clear_phase(self) -> None:
        """Forget the running phase while keeping the cycles of the last hour."""
        self._last_update: float = timestamp()
        self._status: PWMStatus = PWMStatus.IDLE
        self._duty_cycle: Optional[Tuple[int, int]] = None

        self._waiting_for_flame_on: bool = False
        self._last_duty_cycle_percentage: Optional[float] = None
        self._flame_on_at: Optional[float] = None
        self._floor_observed_at: Optional[float] = None

    @property
    def cycles_this_hour(self) -> int:
        """Return the number of duty cycles started in the last hour."""
        return len(self._cycle_starts)

    def enable(self) -> None:
        """Enable the PWM control."""
        self._enabled = True

    def disable(self) -> None:
        """Disable the PWM control."""
        self._clear_phase()
        self._enabled = False

    def suspend(self, timestamp: float) -> None:
        """Pause the cycle while the heater is off, keeping the off-phase timing and the cycles this hour."""
        if self._status == PWMStatus.IDLE:
            return

        if self._status == PWMStatus.ON:
            self._last_update = timestamp
            _LOGGER.info("Suspending PWM Cycle (ON->OFF): heater switched off.")

        self._status = PWMStatus.OFF
        self._flame_on_at = None
        self._floor_observed_at = None
        self._waiting_for_flame_on = False

    def restore(self, state: State) -> None:
        """Restore the PWM controller from a saved state."""
        if enabled := state.attributes.get("pulse_width_modulation_enabled"):
            self._enabled = bool(enabled)
            _LOGGER.debug("Restored Pulse Width Modulation state: %s", enabled)

        self._restore_phase(state.attributes.get("pulse_width_modulation_state"), state.attributes.get("pulse_width_modulation_switched_at"))
        self._restore_effective_on_temperature(state.attributes.get("pulse_width_modulation_effective_on_temperature"))

    def _restore_effective_on_temperature(self, value: Optional[float]) -> None:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return

        if value <= self._heating_system.base_offset:
            return

        self._effective_on_temperature = value
        _LOGGER.debug("Restored effective boiler temperature: %.1f°C", value)

    def _restore_phase(self, status_name: Optional[str], switched_at: Optional[float]) -> None:
        """Resume in the OFF phase from the last switch, so a restart never fires an extra burn."""
        if status_name not in (PWMStatus.ON.name, PWMStatus.OFF.name) or switched_at is None:
            return

        try:
            switched_at = float(switched_at)
        except (TypeError, ValueError):
            return

        if switched_at > timestamp():
            return

        self._status = PWMStatus.OFF
        self._last_update = switched_at

        if status_name == PWMStatus.ON.name:
            self._cycle_starts = [switched_at]

        _LOGGER.info("Resumed PWM in OFF phase from the %s phase that started %.0fs ago.", status_name, timestamp() - switched_at)

    def update(self, device_state: DeviceState, requested_setpoint: float, timestamp: float) -> None:
        """Enable and update the PWM state based on the output of a PID controller."""
        if not self._enabled:
            return

        if device_state.flow_temperature is None:
            self._status = PWMStatus.IDLE
            self._last_update = timestamp

            _LOGGER.warning("PWM turned off due missing values.")

            return

        heating_burn = device_state.flame_active and not device_state.hot_water_active

        if self._status == PWMStatus.ON and heating_burn:
            if self._flame_on_at is None:
                self._flame_on_at = timestamp

            if (timestamp - self._flame_on_at) >= HEATER_STARTUP_TIMEFRAME:
                self._observe_floor(device_state.flow_temperature, timestamp)

        if self._status == PWMStatus.ON and not heating_burn and self._flame_on_at is not None:
            self._flame_on_at = None
            self._floor_observed_at = None
            _LOGGER.info("PWM ON phase lost the heating burn; the next one restarts the warm-up window.")

        elapsed = timestamp - self._last_update
        on_time_seconds, off_time_seconds = self._calculate_duty_cycle(requested_setpoint, device_state)
        self._duty_cycle = (on_time_seconds, off_time_seconds)

        self._cycle_starts = [start for start in self._cycle_starts if (timestamp - start) <= 3600]

        # -------------------------
        # Start ON phase (OFF/IDLE -> ON)
        # -------------------------
        if self._status in (PWMStatus.OFF, PWMStatus.IDLE):
            if on_time_seconds >= HEATER_STARTUP_TIMEFRAME and (self._status == PWMStatus.IDLE or elapsed >= off_time_seconds):
                if self.cycles_this_hour >= self._config.cycles_per_hour:
                    self._status = PWMStatus.OFF
                    _LOGGER.info("Reached max cycles per hour, preventing new duty cycle.")
                    return

                self._cycle_starts.append(timestamp)
                self._status = PWMStatus.ON
                self._last_update = timestamp
                self._waiting_for_flame_on = not device_state.flame_active
                self._flame_on_at = timestamp if device_state.flame_active else None
                self._floor_observed_at = None

                _LOGGER.info(
                    "Starting PWM Cycle (OFF->ON): elapsed=%.0fs active_on=%ds flow=%.1f active_off=%ds",
                    elapsed, on_time_seconds, device_state.flow_temperature, off_time_seconds
                )

                if self._waiting_for_flame_on:
                    _LOGGER.debug("PWM ON phase waiting for flame ignition.")

                return

            if self._status == PWMStatus.IDLE:
                self._status = PWMStatus.OFF

        # -------------------------
        # End ON phase (ON -> OFF)
        # -------------------------
        if self._status == PWMStatus.ON:
            if self._waiting_for_flame_on:
                if device_state.flame_active:
                    self._last_update = timestamp
                    self._flame_on_at = timestamp
                    self._waiting_for_flame_on = False
                    _LOGGER.info("PWM ON phase synced to flame ignition.")
                    return
                elif elapsed < PWM_IGNITION_TIMEOUT_SECONDS:
                    _LOGGER.debug("PWM ON phase still waiting for flame ignition (elapsed=%.0fs).", elapsed)
                    return
                else:
                    self._waiting_for_flame_on = False
                    _LOGGER.warning("PWM ON phase did not detect flame within %ds; starting timer anyway.", PWM_IGNITION_TIMEOUT_SECONDS)

            if self._flame_on_at is not None and (timestamp - self._flame_on_at) < HEATER_STARTUP_TIMEFRAME:
                _LOGGER.debug("PWM ON minimum flame-on hold active (elapsed_since_flame_on=%.0fs/%ds).", timestamp - self._flame_on_at, HEATER_STARTUP_TIMEFRAME)
                return

            if elapsed >= on_time_seconds:
                self._flame_on_at = None
                self._floor_observed_at = None
                self._status = PWMStatus.OFF
                self._last_update = timestamp
                self._waiting_for_flame_on = False

                _LOGGER.info(
                    "Ending PWM Cycle (ON->OFF): elapsed=%.0fs active_on=%ds flow=%.1f active_off=%ds",
                    elapsed, on_time_seconds, device_state.flow_temperature, off_time_seconds
                )
                return

        _LOGGER.debug("Cycle time elapsed: %.0f seconds in state: %s", elapsed, self._status)

    def _observe_floor(self, flow_temperature: float, timestamp: float) -> None:
        """Take the settled flow temperature of an established burn as the boiler floor."""
        if self._floor_observed_at is None:
            self._floor_observed_at = timestamp
            self._effective_on_temperature = flow_temperature
            _LOGGER.debug("Observed boiler floor at %.1f°C", flow_temperature)
            return

        self._effective_on_temperature = 0.3 * flow_temperature + 0.7 * self._effective_on_temperature

    def on_cycle_end(self, cycle: "Cycle") -> None:
        """Adjust PWM enablement based on the last completed cycle classification."""
        if cycle.control_mode == CycleControlMode.CONTINUOUS:
            if cycle.classification == CycleClassification.OVERSHOOT:
                return self.enable()

        if cycle.control_mode == CycleControlMode.PWM:
            if cycle.classification == CycleClassification.UNDERHEAT:
                return self.disable()

    def _calculate_duty_cycle(self, requested_setpoint: float, device_state: DeviceState) -> Tuple[int, int]:
        """Calculate the duty cycle in seconds based on the output of a PID controller and a heating curve value."""
        base_offset = self._heating_system.base_offset
        boiler_temperature = self._effective_on_temperature or device_state.flow_temperature

        if self._effective_on_temperature is None:
            _LOGGER.debug("Boiler floor not learned yet; using the flow temperature of %.1f°C for the duty cycle.", device_state.flow_temperature)

        # Ensure the boiler temperature is above the base offset
        boiler_temperature = max(boiler_temperature, base_offset + 1)

        # Calculate duty cycle percentage
        self._last_duty_cycle_percentage = (requested_setpoint - base_offset) / (boiler_temperature - base_offset)
        self._last_duty_cycle_percentage = min(max(self._last_duty_cycle_percentage, 0), 1)

        _LOGGER.debug(
            "Duty cycle calculation - Requested setpoint: %.1f°C, Effective Boiler temperature: %.1f°C, Duty cycle percentage: %.2f%%",
            requested_setpoint, boiler_temperature, self._last_duty_cycle_percentage * 100
        )

        # Handle special low-duty cycle cases
        if self._last_duty_cycle_percentage < self._min_duty_cycle_percentage:
            if device_state.flame_active and device_state.central_heating and not device_state.hot_water_active:
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
                self.cycles_this_hour, on_time, off_time
            )

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage <= self._duty_cycle_upper_threshold:
            on_time = self._on_time_upper_threshold * self._last_duty_cycle_percentage
            off_time = self._on_time_upper_threshold * (1 - self._last_duty_cycle_percentage)

            _LOGGER.debug(
                "Mid-range duty cycle, cycles this hour: %d. Calculated on_time: %d seconds, off_time: %d seconds.",
                self.cycles_this_hour, on_time, off_time
            )

            return int(on_time), int(off_time)

        if self._last_duty_cycle_percentage <= self._max_duty_cycle_percentage:
            on_time = self._on_time_lower_threshold / (1 - self._last_duty_cycle_percentage) - self._on_time_lower_threshold
            off_time = self._on_time_lower_threshold

            _LOGGER.debug(
                "High duty cycle range, cycles this hour: %d. Calculated on_time: %d seconds, off_time: %d seconds.",
                self.cycles_this_hour, on_time, off_time
            )

            return int(on_time), int(off_time)

        # Handle cases where the duty cycle exceeds the maximum allowed percentage
        on_time = self._on_time_maximum_threshold
        off_time = 0

        _LOGGER.debug("Maximum duty cycle exceeded. Setting on_time: %d seconds, off_time: %d seconds.", on_time, off_time)
        return int(on_time), int(off_time)
