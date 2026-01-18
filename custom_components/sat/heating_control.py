"""Heating control logic for SAT climate entities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant, State

from .const import (
    COLD_SETPOINT,
    MINIMUM_RELATIVE_MODULATION,
    MINIMUM_SETPOINT,
    OVERSHOOT_CYCLES,
)
from .coordinator import SatDataUpdateCoordinator
from .cycles import Cycle, CycleHistory, CycleStatistics, CycleTracker
from .device import DeviceState, DeviceTracker
from .entry_data import SatConfig
from .helpers import event_timestamp, float_value, int_value, timestamp
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
class HeatingDemand:
    """Heating control update."""
    timestamp: float

    hvac_mode: HVACMode
    requested_setpoint: float
    outside_temperature: float


class SatHeatingControl:
    """Coordinate PWM, setpoint, and modulation decisions."""

    def __init__(self, hass: HomeAssistant, coordinator: SatDataUpdateCoordinator, config: SatConfig) -> None:
        self._hass = hass
        self._config = config
        self._coordinator = coordinator

        self._cycles: CycleHistory = CycleHistory()
        self._device_tracker: DeviceTracker = DeviceTracker()

        self._pwm: PWM = PWM(config.pwm, config.heating_system)
        self._cycle_tracker: CycleTracker = CycleTracker(self._hass, self._cycles)

        self._control_setpoint: float = MINIMUM_SETPOINT
        self._relative_modulation_value: int = config.pwm.maximum_relative_modulation

        self._last_requested_setpoint: Optional[float] = None
        self._last_outside_temperature: Optional[float] = None

        self._requested_setpoint_up_ticks: int = 0
        self._requested_setpoint_down_ticks: int = 0

        self._flame_off_hold_setpoint: Optional[float] = None
        self._coordinator_listener_remove: Optional[Callable[[], None]] = None

    @property
    def device_status(self) -> BoilerStatus:
        """Report the current boiler status."""
        return self._device_tracker.status

    @property
    def pwm_state(self) -> PWMState:
        """Expose the current PWM state."""
        return self._pwm.state

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

        pwm_state = self._pwm.status if self._pwm is not None else PWMStatus.IDLE

        if pwm_state == PWMStatus.IDLE:
            if self._coordinator.setpoint is None or self._coordinator.setpoint <= MINIMUM_SETPOINT:
                return RelativeModulationState.COLD

            return RelativeModulationState.PWM_OFF

        return RelativeModulationState.OFF

    @property
    def control_mode(self) -> CycleControlMode:
        if self._pwm.enabled and self._pwm.status != PWMStatus.IDLE:
            return CycleControlMode.PWM

        return CycleControlMode.CONTINUOUS

    async def async_added_to_hass(self) -> None:
        """Register listeners and initialize the control loop."""
        await self._device_tracker.async_added_to_hass(self._hass, self._coordinator.id)

        self._coordinator_listener_remove = self._coordinator.async_add_listener(self._handle_coordinator_update)

    async def async_will_remove_from_hass(self) -> None:
        """Persist state when the integration unloads."""
        if self._coordinator_listener_remove is not None:
            self._coordinator_listener_remove()
            self._coordinator_listener_remove = None

        await self._device_tracker.async_save_data()

    def restore(self, old_state: State) -> None:
        """Restore control state from the persisted climate attributes."""
        if self._pwm is not None:
            self._pwm.restore(old_state)

        if old_state.attributes.get("setpoint") is not None:
            self._control_setpoint = float_value(old_state.attributes.get("setpoint"))

        if old_state.attributes.get("relative_modulation_value") is not None:
            self._relative_modulation_value = int_value(old_state.attributes.get("relative_modulation_value"))

    def reset(self) -> None:
        """Reset heating control state on major changes."""
        self._requested_setpoint_up_ticks = 0
        self._requested_setpoint_down_ticks = 0

        self._pwm.reset()

    async def update(self, demand: HeatingDemand) -> None:
        """Apply a new demand update and push commands to the coordinator."""
        self._last_requested_setpoint = demand.requested_setpoint
        self._last_outside_temperature = demand.outside_temperature

        if demand.hvac_mode != HVACMode.HEAT:
            self._control_setpoint = MINIMUM_SETPOINT
            self._relative_modulation_value = self._config.pwm.maximum_relative_modulation
            self._pwm.disable()
            return

        self._pwm.update(
            timestamp=demand.timestamp,
            device_state=self._coordinator.state,
            requested_setpoint=demand.requested_setpoint,
        )

        if self._cycles.last_cycle is not None:
            if self._cycles.last_cycle.classification in OVERSHOOT_CYCLES:
                self._pwm.enable()

            if (
                    self._cycles.last_cycle.tail.control_setpoint
                    and self._cycles.last_cycle.tail.control_setpoint.p90 is not None
                    and self._cycles.last_cycle.tail.control_setpoint.p90 < demand.requested_setpoint
            ):
                self._pwm.disable()

        self._compute_relative_modulation_value()

        if self.control_mode == CycleControlMode.PWM:
            self._compute_pwm_control_setpoint(demand.requested_setpoint)

        if self.control_mode == CycleControlMode.CONTINUOUS:
            self._compute_continuous_control_setpoint(demand.requested_setpoint)

        await self._coordinator.async_set_heater_state(HeaterState.ON if self._control_setpoint > COLD_SETPOINT else HeaterState.OFF)
        await self._coordinator.async_set_control_max_relative_modulation(self._relative_modulation_value)
        await self._coordinator.async_set_control_setpoint(self._control_setpoint)
        await self._coordinator.async_control_heating_loop(demand.timestamp)

    def _handle_coordinator_update(self, time: Optional[datetime] = None) -> None:
        """Track device and cycle state on coordinator updates."""
        timestamp = event_timestamp(time)

        self._device_tracker.update(
            timestamp=timestamp,
            last_cycle=self.last_cycle,
            state=self._coordinator.state,
        )

        self._cycle_tracker.update(
            ControlLoopSample(
                timestamp=timestamp,
                pwm=self._pwm.state,
                device_state=self._coordinator.state,
                control_setpoint=self._control_setpoint,
                relative_modulation=self._relative_modulation_value,
                requested_setpoint=self._last_requested_setpoint,
                outside_temperature=self._last_outside_temperature,
            )
        )

    def _compute_pwm_control_setpoint(self, requested_setpoint: float) -> None:
        """Apply the PWM setpoint override based on the current device state."""
        config = self._config
        device_state = self._coordinator.state

        if device_state.hot_water_active or requested_setpoint <= COLD_SETPOINT or not config.overshoot_protection:
            return

        if self._pwm.status == PWMStatus.OFF:
            self._control_setpoint = MINIMUM_SETPOINT
            return

        if not config.pwm.dynamic_minimum_setpoint:
            self._control_setpoint = config.limits.minimum_setpoint
            return

        if not device_state.flame_active:
            if (return_temperature := device_state.return_temperature) is None:
                _LOGGER.warning("Setpoint override (flame off): no return temperature found.")
                return

            flame_off_offset = config.flame_off_setpoint_offset_celsius
            self._flame_off_hold_setpoint = return_temperature + flame_off_offset
            self._control_setpoint = self._flame_off_hold_setpoint

            _LOGGER.debug(
                "Setpoint override (flame off): return=%.1f°C offset=%.1f°C -> %.1f°C (intended=%.1f°C)",
                return_temperature, flame_off_offset, self._flame_off_hold_setpoint, requested_setpoint,
            )

            return

        if (flame_on_since := self._device_tracker.flame_on_since) is None:
            _LOGGER.warning("Setpoint override (flame on): no flame on timestamp found.")
            return

        elapsed_since_flame_on = timestamp() - flame_on_since
        suppression_delay = config.modulation_suppression_delay_seconds

        if elapsed_since_flame_on < suppression_delay:
            remaining_hold = suppression_delay - elapsed_since_flame_on
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

        minimum_allowed_setpoint = boiler_temperature - offset
        self._control_setpoint = max(requested_setpoint, minimum_allowed_setpoint)

        if requested_setpoint < minimum_allowed_setpoint:
            _LOGGER.debug(
                "Setpoint clamped to offset minimum (requested=%.1f°C, previous=%.1f°C, boiler=%.1f°C, offset=%.1f°C, applied=%.1f°C)",
                requested_setpoint, previous_setpoint, boiler_temperature, offset, self._control_setpoint,
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
