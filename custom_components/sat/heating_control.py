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
    MINIMUM_SETPOINT, OVERSHOOT_CYCLES, )
from .coordinator import SatDataUpdateCoordinator
from .cycles import CycleHistory, CycleStatistics, CycleTracker, Cycle
from .device import DeviceState, DeviceTracker
from .entry_data import SatConfig
from .helpers import float_value, int_value, timestamp, event_timestamp
from .manufacturers.geminox import Geminox
from .pwm import PWMState, PWM
from .types import BoilerStatus, HeaterState, PWMStatus, RelativeModulationState

_LOGGER = logging.getLogger(__name__)

DECREASE_PERSISTENCE_TICKS = 3
NEAR_TARGET_MARGIN_CELSIUS = 2.0
CONTROL_LOOP_INTERVAL_SECONDS = 10
INCREASE_STEP_THRESHOLD_CELSIUS = 0.5
DECREASE_STEP_THRESHOLD_CELSIUS = 0.5


@dataclass(frozen=True, slots=True)
class ControlLoopSample:
    timestamp: float

    pwm: PWMState
    device_state: DeviceState
    control_setpoint: Optional[float]
    relative_modulation: Optional[float]

    requested_setpoint: Optional[float]
    outside_temperature: Optional[float]


class SatHeatingControl:
    def __init__(self, hass: HomeAssistant, coordinator: SatDataUpdateCoordinator, config: SatConfig) -> None:
        self._hass = hass
        self._config = config
        self._coordinator = coordinator

        self._cycles: CycleHistory = CycleHistory()
        self._device_tracker: DeviceTracker = DeviceTracker()

        self._pwm: PWM = PWM(config.pwm, config.heating_system)
        self._cycle_tracker: CycleTracker = CycleTracker(self._hass, self._cycles)

        self._setpoint: float = MINIMUM_SETPOINT
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
    def cycles(self) -> CycleStatistics:
        """Expose aggregated cycle statistics."""
        return self._cycles.statistics

    @property
    def last_cycle(self) -> Optional[Cycle]:
        """Expose the most recent completed cycle."""
        return self._cycles.last_cycle

    @property
    def pwm(self) -> Optional["PWM"]:
        return self._pwm

    @property
    def setpoint(self) -> Optional[float]:
        return self._setpoint

    @property
    def relative_modulation_value(self) -> Optional[int]:
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

    async def async_added_to_hass(self) -> None:
        """Register listeners and initialize the control loop."""
        await self._device_tracker.async_added_to_hass(self._hass, self._coordinator.id)

        self._coordinator_listener_remove = self._coordinator.async_add_listener(
            self._handle_coordinator_update
        )

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
            self._setpoint = float_value(old_state.attributes.get("setpoint"))

        if old_state.attributes.get("relative_modulation_value") is not None:
            self._relative_modulation_value = int_value(old_state.attributes.get("relative_modulation_value"))

    def reset(self) -> None:
        """Reset heating control state on major changes."""
        self._requested_setpoint_up_ticks = 0
        self._requested_setpoint_down_ticks = 0

        self._pwm.reset()

    async def update(self, hvac_mode: HVACMode, requested_setpoint: float, outside_temperature: float, timestamp: float) -> None:
        self._last_requested_setpoint = requested_setpoint
        self._last_outside_temperature = outside_temperature

        if hvac_mode != HVACMode.HEAT:
            self._setpoint = MINIMUM_SETPOINT
            self._relative_modulation_value = None
            self._pwm.disable()
            return

        self._pwm.update(
            timestamp=timestamp,
            device_state=self._coordinator.state,
            requested_setpoint=requested_setpoint
        )

        if self._cycles.last_cycle is not None:
            if self._cycles.last_cycle.classification in OVERSHOOT_CYCLES:
                self._pwm.enable()

            if (
                    self._cycles.last_cycle.tail.control_setpoint is not None and
                    self._cycles.last_cycle.tail.control_setpoint.p90 is not None and
                    self._cycles.last_cycle.tail.control_setpoint.p90 < requested_setpoint
            ):
                self._pwm.disable()

        self._compute_relative_modulation_value()
        self._compute_control_setpoint(requested_setpoint)

        await self._coordinator.async_set_heater_state(HeaterState.ON if self._setpoint > COLD_SETPOINT else HeaterState.OFF)
        await self._coordinator.async_set_control_max_relative_modulation(self._relative_modulation_value)
        await self._coordinator.async_set_control_setpoint(self._setpoint)
        await self._coordinator.async_control_heating_loop(timestamp)

    def _handle_coordinator_update(self, time: Optional[datetime] = None) -> None:
        timestamp = event_timestamp(time)

        self._device_tracker.update(
            timestamp=timestamp,

            last_cycle=self.last_cycle,
            state=self._coordinator.state,
        )

        self._cycle_tracker.update(ControlLoopSample(
            timestamp=timestamp,

            pwm=self._pwm.state,
            device_state=self._coordinator.state,

            control_setpoint=self._setpoint,
            relative_modulation=self._relative_modulation_value,

            requested_setpoint=self._last_requested_setpoint,
            outside_temperature=self._last_outside_temperature,
        ))

    def _compute_control_setpoint(self, requested_setpoint: float) -> None:
        """Control the setpoint of the heating system based on the current mode and PWM state."""
        if self._pwm.enabled and self._pwm.status != PWMStatus.IDLE:
            self._compute_pwm_control_setpoint(requested_setpoint)
        else:
            self._compute_continuous_control_setpoint(requested_setpoint)

    def _compute_continuous_control_setpoint(self, requested_setpoint):
        _LOGGER.info("Using continuous heating control.")

        requested_setpoint_delta = requested_setpoint - self._setpoint
        if requested_setpoint_delta <= -DECREASE_STEP_THRESHOLD_CELSIUS:
            self._requested_setpoint_down_ticks += 1
        else:
            self._requested_setpoint_down_ticks = 0

        is_near_target = (
                self._coordinator.boiler_temperature is not None
                and self._coordinator.boiler_temperature >= (self._setpoint - NEAR_TARGET_MARGIN_CELSIUS)
        )

        if self._coordinator.flame_active and is_near_target:
            previous_setpoint = self._setpoint
            self._setpoint = max(self._setpoint, requested_setpoint)

            _LOGGER.info(
                "Holding boiler setpoint near target to avoid premature flame-off (requested=%.1f°C, held=%.1f°C, previous=%.1f°C, boiler=%.1f°C, margin=%.1f°C)",
                requested_setpoint, self._setpoint, previous_setpoint, self._coordinator.boiler_temperature, NEAR_TARGET_MARGIN_CELSIUS,
            )

        elif self._coordinator.flame_active and not is_near_target and requested_setpoint_delta < 0:
            _LOGGER.info(
                "Lowering boiler setpoint while still below target flow temperature (requested=%.1f°C, previous=%.1f°C, boiler=%.1f°C)",
                requested_setpoint, self._setpoint, self._coordinator.boiler_temperature,
            )

            self._setpoint = requested_setpoint

        elif requested_setpoint_delta >= INCREASE_STEP_THRESHOLD_CELSIUS:
            _LOGGER.info(
                "Increasing boiler setpoint due to rising heat demand (requested=%.1f°C, previous=%.1f°C)",
                requested_setpoint, self._setpoint,
            )

            self._setpoint = requested_setpoint

        elif self._requested_setpoint_down_ticks >= DECREASE_PERSISTENCE_TICKS:
            _LOGGER.info(
                "Lowering boiler setpoint after sustained lower demand (requested=%.1f°C persisted for %d cycles)",
                requested_setpoint, self._requested_setpoint_down_ticks,
            )

            self._setpoint = requested_setpoint

        elif not self._coordinator.flame_active:
            _LOGGER.info(
                "Updating boiler setpoint while flame is off (requested=%.1f°)",
                requested_setpoint
            )

            self._setpoint = requested_setpoint
            self._requested_setpoint_up_ticks = 0
            self._requested_setpoint_down_ticks = 0

    def _compute_pwm_control_setpoint(self, requested_setpoint: float) -> None:
        """Apply a setpoint override based on the current device state."""
        device_state = self._coordinator.state

        if self._pwm.status == PWMStatus.OFF:
            self._setpoint = MINIMUM_SETPOINT
            return

        if device_state.hot_water_active or requested_setpoint <= COLD_SETPOINT:
            return

        if not self._config.overshoot_protection:
            return

        if not self._config.pwm.dynamic_minimum_setpoint:
            self._setpoint = self._config.limits.minimum_setpoint
            return

        if not device_state.flame_active:
            if (return_temperature := device_state.return_temperature) is None:
                _LOGGER.warning("Setpoint override (flame off): no return temperature found.")
                return

            self._flame_off_hold_setpoint = return_temperature + self._config.flame_off_setpoint_offset_celsius
            self._setpoint = self._flame_off_hold_setpoint

            _LOGGER.debug(
                "Setpoint override (flame off): return=%.1f°C offset=%.1f°C -> %.1f°C (intended=%.1f°C)",
                return_temperature, self._config.flame_off_setpoint_offset_celsius, self._flame_off_hold_setpoint, requested_setpoint,
            )

            return

        if (flame_on_since := self._device_tracker.flame_on_since) is None:
            _LOGGER.warning("Setpoint override (flame on): no flame on timestamp found.")
            return

        elapsed_since_flame_on = timestamp() - flame_on_since
        if elapsed_since_flame_on < self._config.modulation_suppression_delay_seconds:
            if self._flame_off_hold_setpoint is not None:
                _LOGGER.debug(
                    "Setpoint override hold: using flame-off setpoint %.1f°C for %.1fs more.",
                    self._flame_off_hold_setpoint, self._config.modulation_suppression_delay_seconds - elapsed_since_flame_on,
                )

                self._setpoint = self._flame_off_hold_setpoint
                return

            _LOGGER.debug(
                "Setpoint override pending: waiting %.1fs more (elapsed=%.1fs, delay=%.1fs).",
                self._config.modulation_suppression_delay_seconds - elapsed_since_flame_on, elapsed_since_flame_on, self._config.modulation_suppression_delay_seconds,
            )

            return

        if (flow_temperature := device_state.flow_temperature) is None:
            _LOGGER.warning("Setpoint override (flow temperature): no flow temperature found.")
            return

        self._flame_off_hold_setpoint = None
        suppressed_setpoint = flow_temperature - self._config.modulation_suppression_offset_celsius

        _LOGGER.debug(
            "Setpoint override (suppression) : flow=%.1f°C offset=%.1f°C -> %.1f°C (min=%.1f°C, intended=%.1f°C)",
            flow_temperature, self._config.modulation_suppression_offset_celsius, suppressed_setpoint, requested_setpoint, requested_setpoint,
        )

        self._setpoint = suppressed_setpoint

    def _compute_relative_modulation_value(self) -> None:
        """Control the relative modulation value based on the conditions."""
        if not self._coordinator.supports_relative_modulation_management:
            self._relative_modulation_value = None
            _LOGGER.debug("Relative modulation management is not supported. Skipping control.")
            return

        requested_value = self._requested_relative_modulation_value()

        if isinstance(self._coordinator.manufacturer, Geminox):
            requested_value = max(10, requested_value)

        self._relative_modulation_value = requested_value

        if self._coordinator.maximum_relative_modulation_value == self._relative_modulation_value:
            _LOGGER.debug("Relative modulation value unchanged (%d%%). No update necessary.", self._relative_modulation_value)

    def _requested_relative_modulation_value(self) -> int:
        """Return the capped maximum relative modulation value."""
        if not self._coordinator.supports_relative_modulation_management or self.relative_modulation_state != RelativeModulationState.OFF:
            return self._config.pwm.maximum_relative_modulation

        return MINIMUM_RELATIVE_MODULATION
