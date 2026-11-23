"""Heating control logic for SAT climate entities."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Optional

from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.dispatcher import dispatcher_send

from .boiler_health import BoilerHealthMonitor
from .const import (
    COLD_SETPOINT,
    HEATER_STARTUP_TIMEFRAME,
    DHW_OVERSHOOT_GUARD_SECONDS,
    MINIMUM_RELATIVE_MODULATION,
    MINIMUM_SETPOINT, EVENT_SAT_CYCLE_ENDED, UNDERHEAT_SUSTAIN_SECONDS, SATURATION_SUSTAIN_SECONDS, OVERSHOOT_SUSTAIN_SECONDS,
    TARGET_TEMPERATURE_CHANGE_WINDOW_SECONDS,
    OVERSHOOT_REFERENCE_WINDOW_SECONDS,
    SIGNAL_HEATING_CONTROL_UPDATED,
)
from .coordinator import SatDataUpdateCoordinator
from .cycles import Cycle, CycleHistory, CycleStatistics, CycleTracker
from .cycles.const import OVERSHOOT_MARGIN_CELSIUS, UNDERSHOOT_MARGIN_CELSIUS
from .device import DeviceState, DeviceTracker
from .entry_data import SatConfig
from .helpers import (
    clamp,
    event_timestamp,
    float_value,
    int_value,
    is_within_elapsed_window,
    sustained_runtime,
    timestamp,
)
from .manufacturers.geminox import Geminox
from .pwm import PWM, PWMState
from .types import BoilerStatus, CycleControlMode, HeaterState, PWMStatus, RelativeModulationState

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ControlLoopSample:
    """Snapshot used by the control loop tracker."""
    timestamp: float

    pwm: PWMState
    device_state: DeviceState
    control_setpoint: Optional[float]
    relative_modulation: Optional[float]

    requested_setpoint: Optional[float]
    outside_temperature: Optional[float]


@dataclass(frozen=True, slots=True)
class RequestedSetpointSample:
    """Requested flow setpoint at a moment in time."""

    timestamp: float
    setpoint: float


@dataclass(frozen=True, slots=True)
class HeatingDemand:
    """Heating control update."""
    timestamp: float

    valves_open: bool
    hvac_mode: HVACMode
    requested_setpoint: float
    outside_temperature: Optional[float]
    target_temperature_changed_at: Optional[float] = None

    @property
    def heater_state(self) -> HeaterState:
        if self.hvac_mode != HVACMode.HEAT:
            return HeaterState.OFF

        if not self.valves_open:
            return HeaterState.OFF

        return HeaterState.ON if self.requested_setpoint > COLD_SETPOINT else HeaterState.OFF


class SatHeatingControl:
    """Coordinate PWM, setpoint, and modulation decisions."""

    def __init__(self, hass: HomeAssistant, coordinator: SatDataUpdateCoordinator, config: SatConfig) -> None:
        self._hass = hass
        self._config = config
        self._coordinator = coordinator

        self._cycles: CycleHistory = CycleHistory()
        self._device_tracker: DeviceTracker = DeviceTracker()
        self._boiler_health: BoilerHealthMonitor = BoilerHealthMonitor(config)

        self._pwm: PWM = PWM(config.pwm, config.heating_system)
        self._cycle_tracker: CycleTracker = CycleTracker(self._hass, self._cycles)

        self._control_setpoint: float = MINIMUM_SETPOINT
        self._relative_modulation_value: int = config.pwm.maximum_relative_modulation

        self._last_requested_setpoint: Optional[float] = None
        self._last_outside_temperature: Optional[float] = None
        self._requested_setpoints: deque[RequestedSetpointSample] = deque()

        self._flame_off_hold_setpoint: Optional[float] = None
        self._coordinator_listener_remove: Optional[Callable[[], None]] = None
        self._pwm_cycle_listener_remove: Optional[Callable[[], None]] = None

        self._sustained_overshoot_started_at: Optional[float] = None
        self._sustained_underheat_started_at: Optional[float] = None
        self._sustained_saturation_started_at: Optional[float] = None

    @property
    def device_status(self) -> BoilerStatus:
        """Report the current boiler status."""
        return self._device_tracker.status

    @property
    def pwm_state(self) -> PWMState:
        """Expose the current PWM state."""
        return self._pwm.state

    def enable_pwm(self) -> None:
        """Enable Pulse Width Modulation."""
        self._pwm.enable()

    def disable_pwm(self) -> None:
        """Disable Pulse Width Modulation."""
        self._pwm.disable()
        self._reset_pwm_disable_guards()

    @property
    def boiler_health(self) -> BoilerHealthMonitor:
        """Expose the long-term boiler health monitor."""
        return self._boiler_health

    @property
    def cycles(self) -> CycleStatistics:
        """Expose aggregated cycle statistics."""
        return self._cycles.statistics

    @property
    def last_cycle(self) -> Optional[Cycle]:
        """Expose the most recent completed cycle."""
        return self._cycles.last_cycle

    @property
    def control_setpoint(self) -> Optional[float]:
        """Expose the last computed control setpoint."""
        return self._control_setpoint

    @property
    def relative_modulation_value(self) -> int:
        """Expose the last computed relative modulation value."""
        return self._relative_modulation_value

    @property
    def relative_modulation_state(self) -> RelativeModulationState:
        """Return the computed relative modulation state."""
        if self._coordinator.hot_water_active:
            return RelativeModulationState.HOT_WATER

        if self._pwm.enabled:
            return RelativeModulationState.OFF

        if self._coordinator.setpoint is None or self._coordinator.setpoint <= MINIMUM_SETPOINT:
            return RelativeModulationState.COLD

        return RelativeModulationState.PWM_OFF

    @property
    def control_mode(self) -> CycleControlMode:
        if self._pwm.enabled and self._pwm.status != PWMStatus.IDLE:
            return CycleControlMode.PWM

        return CycleControlMode.CONTINUOUS

    @property
    def _pulse_width_modulation_forced(self) -> bool:
        return self._config.pwm.force_pulse_width_modulation or not self._coordinator.supports_setpoint_management

    @property
    def _pwm_device_state(self) -> DeviceState:
        device_state = self._coordinator.state

        if device_state.flow_temperature is not None or self._coordinator.supports_setpoint_management:
            return device_state

        return replace(device_state, flow_temperature=self._coordinator.minimum_setpoint)

    async def async_added_to_hass(self) -> None:
        """Register listeners and initialize the control loop."""
        await self._device_tracker.async_added_to_hass(self._hass, self._coordinator.id)
        await self._boiler_health.async_added_to_hass(self._hass, self._config.entry_id)

        if self._coordinator_listener_remove is not None:
            self._coordinator_listener_remove()
            self._coordinator_listener_remove = None

        if self._pwm_cycle_listener_remove is not None:
            self._pwm_cycle_listener_remove()
            self._pwm_cycle_listener_remove = None

        self._coordinator_listener_remove = self._coordinator.async_add_listener(self._handle_coordinator_update)
        self._pwm_cycle_listener_remove = self._hass.bus.async_listen(EVENT_SAT_CYCLE_ENDED, self._on_cycle_ended)

        self._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        """Persist state when the integration unloads."""
        if self._coordinator_listener_remove is not None:
            self._coordinator_listener_remove()
            self._coordinator_listener_remove = None

        if self._pwm_cycle_listener_remove is not None:
            self._pwm_cycle_listener_remove()
            self._pwm_cycle_listener_remove = None

        await self._device_tracker.async_save_data()
        await self._boiler_health.async_save_data()

    def restore(self, old_state: State) -> None:
        """Restore control state from the persisted climate attributes."""
        self._pwm.restore(old_state)

        if old_state.attributes.get("setpoint") is not None:
            self._control_setpoint = float_value(old_state.attributes.get("setpoint"))

        if old_state.attributes.get("relative_modulation_value") is not None:
            self._relative_modulation_value = int_value(old_state.attributes.get("relative_modulation_value"))

    def reset(self) -> None:
        """Reset heating control state on major changes."""
        self._pwm.reset()
        self._reset_pwm_disable_guards()

    async def update(self, demand: HeatingDemand) -> None:
        """Apply a new demand update and push commands to the coordinator."""
        self._last_requested_setpoint = demand.requested_setpoint
        self._last_outside_temperature = demand.outside_temperature
        self._record_requested_setpoint(demand)

        if demand.heater_state == HeaterState.ON:
            if self._pulse_width_modulation_forced:
                self._pwm.enable()
            elif demand.requested_setpoint >= self._config.limits.maximum_setpoint:
                self._pwm.disable()

            self._maybe_enable_pwm_on_sustained_overshoot(
                demand=demand,
                device_state=self._coordinator.state
            )

            self._pwm.update(
                timestamp=demand.timestamp,
                device_state=self._pwm_device_state,
                requested_setpoint=demand.requested_setpoint,
            )

            self._maybe_disable_pwm_runtime(
                demand=demand,
                device_state=self._coordinator.state
            )

            self._compute_relative_modulation_value()

            if self.control_mode == CycleControlMode.PWM:
                self._compute_pwm_control_setpoint(demand.requested_setpoint)

            if self.control_mode == CycleControlMode.CONTINUOUS:
                self._compute_continuous_control_setpoint(demand.requested_setpoint)
        else:
            self._pwm.suspend(demand.timestamp)
            self._reset_pwm_disable_guards()

            self._control_setpoint = MINIMUM_SETPOINT
            self._relative_modulation_value = self._config.pwm.maximum_relative_modulation

        await self._coordinator.async_set_control_setpoint(self._control_setpoint)
        await self._coordinator.async_set_control_max_relative_modulation(self._relative_modulation_value)
        await self._coordinator.async_set_heater_state(HeaterState.ON if self._control_setpoint > COLD_SETPOINT else HeaterState.OFF)

        await self._coordinator.async_control_heating_loop(demand.timestamp)

    def _handle_coordinator_update(self, time: Optional[datetime] = None) -> None:
        """Track device and cycle state on coordinator updates."""
        self._device_tracker.update(
            timestamp=event_timestamp(time),
            last_cycle=self.last_cycle,
            state=self._coordinator.state,
        )

        self._cycle_tracker.update(
            ControlLoopSample(
                timestamp=event_timestamp(time),
                pwm=self._pwm.state,
                device_state=self._coordinator.state,
                control_setpoint=self._control_setpoint,
                requested_setpoint=self._last_requested_setpoint,
                outside_temperature=self._last_outside_temperature,
                relative_modulation=self._relative_modulation_value,
            )
        )

        self._boiler_health.update(self._coordinator.state, self._coordinator.boiler_pressure, event_timestamp(time))

        dispatcher_send(self._hass, SIGNAL_HEATING_CONTROL_UPDATED, self._coordinator.id)

    def _on_cycle_ended(self, event) -> None:
        """Forward completed cycles to the PWM controller."""
        if (cycle := event.data.get("cycle")) is None:
            return

        sample = event.data.get("sample")
        self._boiler_health.record_cycle(cycle, sample.outside_temperature if sample is not None else None)

        if not self._config.overshoot_protection and cycle.control_mode == CycleControlMode.CONTINUOUS:
            return

        if self._pulse_width_modulation_forced and cycle.control_mode == CycleControlMode.PWM:
            return

        self._pwm.on_cycle_end(cycle=cycle)

    def _maybe_enable_pwm_on_sustained_overshoot(self, demand: HeatingDemand, device_state: DeviceState) -> None:
        """Enable PWM when the boiler stays in overshoot while flame is active."""
        if not self._config.overshoot_protection:
            return

        if self._pwm.enabled:
            self._sustained_overshoot_started_at = None
            return

        if device_state.hot_water_active:
            self._sustained_overshoot_started_at = None
            return

        if is_within_elapsed_window(demand.timestamp, self._device_tracker.hot_water_off_since, DHW_OVERSHOOT_GUARD_SECONDS):
            self._sustained_overshoot_started_at = None
            return

        if demand.heater_state != HeaterState.ON:
            self._sustained_overshoot_started_at = None
            return

        if demand.requested_setpoint <= COLD_SETPOINT:
            self._sustained_overshoot_started_at = None
            return

        if device_state.flow_temperature is None:
            self._sustained_overshoot_started_at = None
            return

        overshoot_threshold = self._recent_requested_setpoint_maximum() + OVERSHOOT_MARGIN_CELSIUS
        if device_state.flow_temperature < overshoot_threshold:
            self._sustained_overshoot_started_at = None
            return

        runtime = sustained_runtime(demand.timestamp, self._sustained_overshoot_started_at)
        self._sustained_overshoot_started_at = runtime.started_at

        if runtime.initialized or runtime.elapsed_seconds < OVERSHOOT_SUSTAIN_SECONDS:
            return

        _LOGGER.info(
            "Sustained overshoot detected (flow=%.1f°C >= requested=%.1f°C + %.1f°C for %.0fs).",
            device_state.flow_temperature, demand.requested_setpoint, OVERSHOOT_MARGIN_CELSIUS, runtime.elapsed_seconds
        )

        self._pwm.enable()
        self._reset_pwm_disable_guards()

    def _record_requested_setpoint(self, demand: HeatingDemand) -> None:
        self._requested_setpoints.append(RequestedSetpointSample(timestamp=demand.timestamp, setpoint=demand.requested_setpoint))

        while self._requested_setpoints and (demand.timestamp - self._requested_setpoints[0].timestamp) > OVERSHOOT_REFERENCE_WINDOW_SECONDS:
            self._requested_setpoints.popleft()

    def _recent_requested_setpoint_maximum(self) -> float:
        """Return the highest setpoint asked of the boiler recently, since it cannot shed heat as fast as the request can fall."""
        return max(sample.setpoint for sample in self._requested_setpoints)

    def _maybe_disable_pwm_runtime(self, demand: HeatingDemand, device_state: DeviceState) -> None:
        if self._pulse_width_modulation_forced:
            return

        if not self._pwm.enabled:
            self._sustained_underheat_started_at = None
            self._sustained_saturation_started_at = None
            return

        if device_state.hot_water_active or is_within_elapsed_window(demand.timestamp, self._device_tracker.hot_water_off_since, DHW_OVERSHOOT_GUARD_SECONDS):
            self._reset_pwm_disable_guards()
            return

        if demand.heater_state != HeaterState.ON or demand.requested_setpoint <= COLD_SETPOINT:
            self._reset_pwm_disable_guards()
            return

        if self._pwm.status != PWMStatus.ON:
            self._reset_pwm_disable_guards()
            return

        if self._maybe_disable_pwm_on_sustained_underheat(demand=demand, device_state=device_state):
            return

        if self._maybe_disable_pwm_on_sustained_saturation(demand=demand):
            return

    def _maybe_disable_pwm_on_sustained_underheat(self, demand: HeatingDemand, device_state: DeviceState) -> bool:
        flow_temperature = device_state.flow_temperature
        if flow_temperature is None or not self._flame_established(demand.timestamp, device_state):
            self._sustained_underheat_started_at = None
            return False

        underheat_threshold = demand.requested_setpoint + UNDERSHOOT_MARGIN_CELSIUS
        if flow_temperature > underheat_threshold:
            self._sustained_underheat_started_at = None
            return False

        runtime = sustained_runtime(demand.timestamp, self._sustained_underheat_started_at)
        self._sustained_underheat_started_at = runtime.started_at

        if runtime.initialized or runtime.elapsed_seconds < UNDERHEAT_SUSTAIN_SECONDS:
            return False

        _LOGGER.info(
            "Disabling PWM due to sustained underheat (flow=%.1f°C <= requested=%.1f°C + %.1f°C for %.0fs).",
            flow_temperature, demand.requested_setpoint, UNDERSHOOT_MARGIN_CELSIUS, runtime.elapsed_seconds,
        )

        self._pwm.disable()
        self._reset_pwm_disable_guards()

        return True

    def burned_within_pwm_period(self, now: float) -> bool:
        """Whether the burner is on or last ran no longer ago than the off phase of a PWM cycle can explain."""
        if self._coordinator.flame_active:
            return True

        if (flame_off_since := self._device_tracker.flame_off_since) is None:
            return False

        return (now - flame_off_since) <= self._pwm.maximum_cycle_seconds

    def _flame_established(self, now: float, device_state: DeviceState) -> bool:
        """Whether the burner has been running long enough for the flow temperature to reflect its output."""
        if not device_state.flame_active or (flame_on_since := self._device_tracker.flame_on_since) is None:
            return False

        return (now - flame_on_since) >= HEATER_STARTUP_TIMEFRAME

    def _maybe_disable_pwm_on_sustained_saturation(self, demand: HeatingDemand) -> bool:
        pwm_state = self._pwm.state
        off_time_seconds = pwm_state.off_time_seconds
        if off_time_seconds is None or off_time_seconds > 0:
            self._sustained_saturation_started_at = None
            return False

        if not self._saturation_is_evidence(demand, pwm_state):
            self._sustained_saturation_started_at = None
            return False

        if is_within_elapsed_window(demand.timestamp, demand.target_temperature_changed_at, TARGET_TEMPERATURE_CHANGE_WINDOW_SECONDS):
            _LOGGER.info("Disabling PWM: the duty cycle saturated right after the target temperature changed.")

            self._pwm.disable()
            self._reset_pwm_disable_guards()

            return True

        runtime = sustained_runtime(demand.timestamp, self._sustained_saturation_started_at)
        self._sustained_saturation_started_at = runtime.started_at

        if runtime.initialized or runtime.elapsed_seconds < SATURATION_SUSTAIN_SECONDS:
            return False

        _LOGGER.info(
            "Disabling PWM due to sustained saturation (off_time=%ds for %.0fs).",
            off_time_seconds, runtime.elapsed_seconds
        )

        self._pwm.disable()
        self._reset_pwm_disable_guards()

        return True

    @staticmethod
    def _saturation_is_evidence(demand: HeatingDemand, pwm_state: PWMState) -> bool:
        """Whether a saturated duty cycle rests on an observed boiler floor, or on a prior the request clearly exceeds."""
        if not pwm_state.floor_known:
            _LOGGER.debug("Duty cycle saturated without a learned boiler floor; not evidence.")
            return False

        if pwm_state.floor_observed:
            return True

        return demand.requested_setpoint >= pwm_state.effective_on_temperature + OVERSHOOT_MARGIN_CELSIUS

    def _compute_pwm_control_setpoint(self, requested_setpoint: float) -> None:
        """Apply the PWM setpoint override based on the current device state."""
        config = self._config
        device_state = self._coordinator.state

        if device_state.hot_water_active or requested_setpoint <= COLD_SETPOINT:
            return

        if self._pwm.status == PWMStatus.OFF:
            self._control_setpoint = MINIMUM_SETPOINT
            return

        if not self._coordinator.supports_setpoint_management:
            self._control_setpoint = requested_setpoint
            return

        if not device_state.flame_active:
            if (return_temperature := device_state.return_temperature) is None:
                _LOGGER.debug(
                    "Setpoint override (flame off): no return temperature found, falling back to the requested setpoint."
                )

                self._control_setpoint = requested_setpoint

                return

            flame_off_offset = config.flame_off_setpoint_offset_celsius
            flame_off_hold_setpoint = clamp(return_temperature + flame_off_offset, COLD_SETPOINT, self._config.limits.maximum_setpoint)

            self._control_setpoint = flame_off_hold_setpoint
            self._flame_off_hold_setpoint = flame_off_hold_setpoint

            _LOGGER.debug(
                "Setpoint override (flame off): return=%.1f°C offset=%.1f°C -> %.1f°C (intended=%.1f°C)",
                return_temperature, flame_off_offset, self._flame_off_hold_setpoint, requested_setpoint,
            )

            return

        elapsed_since_flame_on = self._elapsed_since_flame_on()
        suppression_delay = config.modulation_suppression_delay_seconds

        if elapsed_since_flame_on < suppression_delay:
            remaining_hold = suppression_delay - elapsed_since_flame_on
            if self._flame_off_hold_setpoint is None and (return_temperature := device_state.return_temperature) is not None:
                self._flame_off_hold_setpoint = clamp(return_temperature + config.flame_off_setpoint_offset_celsius, COLD_SETPOINT, self._config.limits.maximum_setpoint)

            if self._flame_off_hold_setpoint is not None:
                _LOGGER.debug(
                    "Setpoint override hold: using flame-off setpoint %.1f°C for %.1fs more.",
                    self._flame_off_hold_setpoint, remaining_hold,
                )

                self._control_setpoint = self._flame_off_hold_setpoint
                return

            _LOGGER.debug(
                "Setpoint override pending: waiting %.1fs more (elapsed=%.1fs, delay=%.1fs).",
                remaining_hold, elapsed_since_flame_on, suppression_delay,
            )

            return

        if (flow_temperature := device_state.flow_temperature) is None:
            _LOGGER.warning("Setpoint override (flow temperature): no flow temperature found.")
            return

        self._flame_off_hold_setpoint = None
        flow_offset = config.modulation_suppression_offset_celsius
        suppressed_setpoint = flow_temperature - flow_offset

        _LOGGER.debug(
            "Setpoint override (suppression): flow=%.1f°C offset=%.1f°C -> %.1f°C (requested=%.1f°C)",
            flow_temperature, flow_offset, suppressed_setpoint, requested_setpoint,
        )

        self._control_setpoint = suppressed_setpoint

    def _elapsed_since_flame_on(self) -> float:
        """The tracker may not have seen the current ignition yet, in which case the flame has just come on."""
        flame_on_since = self._device_tracker.flame_on_since
        flame_off_since = self._device_tracker.flame_off_since

        if flame_on_since is None or (flame_off_since is not None and flame_on_since < flame_off_since):
            return 0.0

        return timestamp() - flame_on_since

    def _compute_continuous_control_setpoint(self, requested_setpoint: float) -> None:
        """Apply the continuous setpoint update based on boiler temperature."""
        _LOGGER.debug("Using continuous heating control.")

        previous_setpoint = self._control_setpoint
        offset = self._config.flow_setpoint_offset_celsius
        boiler_temperature = self._coordinator.boiler_temperature

        if boiler_temperature is None:
            self._control_setpoint = requested_setpoint

            _LOGGER.debug(
                "Setpoint update skipped boiler clamp due to missing boiler temperature (requested=%.1f°C, previous=%.1f°C)",
                requested_setpoint, previous_setpoint,
            )
            return

        if boiler_temperature <= requested_setpoint:
            self._control_setpoint = requested_setpoint

            _LOGGER.debug(
                "Setpoint followed request at/above boiler temperature (requested=%.1f°C, previous=%.1f°C, boiler=%.1f°C)",
                requested_setpoint, previous_setpoint, boiler_temperature,
            )
            return

        if not self._coordinator.flame_active:
            self._control_setpoint = requested_setpoint

            _LOGGER.debug(
                "Setpoint followed request while flame is off (requested=%.1f°C, previous=%.1f°C, boiler=%.1f°C)",
                requested_setpoint, previous_setpoint, boiler_temperature,
            )
            return

        minimum_allowed_setpoint = boiler_temperature - offset
        if requested_setpoint > previous_setpoint:
            self._control_setpoint = max(requested_setpoint, minimum_allowed_setpoint)
        else:
            self._control_setpoint = max(requested_setpoint, min(minimum_allowed_setpoint, previous_setpoint))

        if requested_setpoint < minimum_allowed_setpoint and self._control_setpoint == minimum_allowed_setpoint:
            _LOGGER.debug(
                "Setpoint clamped to offset minimum (requested=%.1f°C, previous=%.1f°C, boiler=%.1f°C, offset=%.1f°C, applied=%.1f°C)",
                requested_setpoint, previous_setpoint, boiler_temperature, offset, self._control_setpoint,
            )
            return

        if requested_setpoint < minimum_allowed_setpoint and self._control_setpoint == previous_setpoint:
            _LOGGER.debug(
                "Setpoint held to avoid increase despite offset minimum (requested=%.1f°C, previous=%.1f°C, boiler=%.1f°C, offset=%.1f°C, minimum=%.1f°C)",
                requested_setpoint, previous_setpoint, boiler_temperature, offset, minimum_allowed_setpoint,
            )
            return

        _LOGGER.debug(
            "Setpoint followed request below boiler temperature (requested=%.1f°C, previous=%.1f°C, boiler=%.1f°C, offset=%.1f°C)",
            requested_setpoint, previous_setpoint, boiler_temperature, offset,
        )

    def _compute_relative_modulation_value(self) -> None:
        """Control the relative modulation value based on the conditions."""
        if not self._coordinator.supports_relative_modulation_management:
            self._relative_modulation_value = None
            _LOGGER.debug("Relative modulation management is not supported. Skipping control.")
            return

        if self.relative_modulation_state == RelativeModulationState.OFF:
            self._relative_modulation_value = MINIMUM_RELATIVE_MODULATION
        else:
            self._relative_modulation_value = self._config.pwm.maximum_relative_modulation

        if isinstance(self._coordinator.manufacturer, Geminox):
            self._relative_modulation_value = max(10, self._relative_modulation_value)

    def _reset_pwm_disable_guards(self) -> None:
        self._sustained_overshoot_started_at = None
        self._sustained_underheat_started_at = None
        self._sustained_saturation_started_at = None
