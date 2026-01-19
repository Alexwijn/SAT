from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from .const import *
from .types import DeviceState
from ..types import BoilerStatus

if TYPE_CHECKING:
    from ..cycles import Cycle


@dataclass(frozen=True, slots=True)
class DeviceStatusSnapshot:
    last_cycle: Optional["Cycle"]
    last_flame_on_at: Optional[float]
    last_flame_off_at: Optional[float]
    last_flame_off_was_overshoot: bool
    last_update_at: Optional[float]
    previous_update_at: Optional[float]
    modulation_direction: int
    previous_state: Optional[DeviceState]
    state: DeviceState


class DeviceStatusEvaluator:
    @staticmethod
    def evaluate(snapshot: DeviceStatusSnapshot) -> BoilerStatus:
        state = snapshot.state
        previous = snapshot.previous_state

        # Power / availability
        if not state.central_heating:
            return BoilerStatus.OFF

        if not state.flame_active:
            # Overshoot cooling: flame off due to overshoot, still above setpoint.
            if DeviceStatusEvaluator.is_overshoot_cooling(state, snapshot.last_flame_off_was_overshoot):
                return BoilerStatus.OVERSHOOT_COOLING

            # Anti-cycling: off despite demand, within minimum off time.
            if DeviceStatusEvaluator.is_in_anti_cycling(state, snapshot.last_update_at, snapshot.last_flame_off_at):
                return BoilerStatus.ANTI_CYCLING

            # Stalled ignition: OFF for much longer than expected, with demand present.
            if DeviceStatusEvaluator.is_ignition_stalled(
                    last_cycle=snapshot.last_cycle,
                    last_flame_off_at=snapshot.last_flame_off_at,
                    last_flame_off_was_overshoot=snapshot.last_flame_off_was_overshoot,
                    last_update_at=snapshot.last_update_at,
                    state=state,
            ):
                return BoilerStatus.STALLED_IGNITION

            # Flame just turned off, and we are not overshoot cooling nor anti cycling.
            if previous is not None and previous.flame_active:
                return BoilerStatus.COOLING

            # Just became active → pump starting phase.
            if DeviceStatusEvaluator.is_pump_start_phase(state, previous, snapshot.last_flame_on_at):
                return BoilerStatus.PUMP_STARTING

            # Waiting for flame: active, demand present, not anti cycling, not stalled.
            if DeviceStatusEvaluator.has_demand(state):
                return BoilerStatus.WAITING_FOR_FLAME

            # Post-cycle settling: shortly after last off, no demand yet.
            if DeviceStatusEvaluator.is_in_post_cycle_settling(state, snapshot.last_update_at, snapshot.last_flame_off_at):
                return BoilerStatus.POST_CYCLE_SETTLING

            # Otherwise, simply idle.
            return BoilerStatus.IDLE

        if state.hot_water_active:
            return BoilerStatus.HEATING_HOT_WATER

        # Space heating with flame on.
        if state.setpoint is None or state.flow_temperature is None:
            # Without temperatures, we cannot distinguish phases well.
            return BoilerStatus.CENTRAL_HEATING

        if DeviceStatusEvaluator.is_ramping_up(state, previous, snapshot.last_flame_on_at, snapshot.last_update_at, snapshot.previous_update_at):
            return BoilerStatus.IGNITION_SURGE

        delta_to_setpoint = state.setpoint - state.flow_temperature

        # Preheating: far below the setpoint.
        if delta_to_setpoint > BOILER_PREHEAT_DELTA:
            return BoilerStatus.PREHEATING

        # At-setpoint band: very close to setpoint.
        if abs(delta_to_setpoint) <= BOILER_SETPOINT_BAND:
            return BoilerStatus.AT_SETPOINT_BAND

        # Otherwise: direction of modulation (up/down) based on modulation or gradients.
        if snapshot.modulation_direction > 0:
            return BoilerStatus.MODULATING_UP

        if snapshot.modulation_direction < 0:
            return BoilerStatus.MODULATING_DOWN

        # Fallback: generic heating space.
        return BoilerStatus.CENTRAL_HEATING

    @staticmethod
    def did_overshoot_at_flame_off(state: DeviceState) -> bool:
        """Return True if the flame turned off because of overshoot."""
        if state.setpoint is None or state.flow_temperature is None:
            return False

        return state.flow_temperature >= state.setpoint + BOILER_OVERSHOOT_DELTA

    @staticmethod
    def has_demand(state: DeviceState) -> bool:
        """Return True if space-heating demand is present."""
        if state.setpoint is None or state.flow_temperature is None:
            return False

        return state.setpoint > state.flow_temperature + BOILER_DEMAND_HYSTERESIS

    @staticmethod
    def is_ignition_stalled(last_cycle: Optional["Cycle"], last_flame_off_at: Optional[float], last_flame_off_was_overshoot: bool, last_update_at: Optional[float], state: DeviceState) -> bool:
        """Detect stalled ignition when demand persists for too long."""
        if last_flame_off_at is None or last_update_at is None:
            return False

        if state.flame_active:
            return False

        if not DeviceStatusEvaluator.has_demand(state):
            return False

        # If we are still in anti-cycling or overshoot cooling, do not call this stalled.
        if DeviceStatusEvaluator.is_in_anti_cycling(state, last_update_at, last_flame_off_at):
            return False

        if DeviceStatusEvaluator.is_overshoot_cooling(state, last_flame_off_was_overshoot):
            return False

        time_since_off = last_update_at - last_flame_off_at
        if time_since_off < 0:
            return False

        # Base threshold on the last cycle duration (if available) plus an absolute floor.
        threshold = BOILER_STALL_IGNITION_MIN_OFF_SECONDS

        if last_cycle is not None:
            try:
                last_duration = float(last_cycle.duration)
                threshold = max(threshold, last_duration * BOILER_STALL_IGNITION_OFF_RATIO)
            except (TypeError, ValueError):
                # Ignore if duration_seconds is missing or not numeric.
                pass

        return time_since_off >= threshold

    @staticmethod
    def is_in_anti_cycling(state: DeviceState, last_update_at: Optional[float], last_flame_off_at: Optional[float]) -> bool:
        """Return True when boiler is in enforced anti-cycling off-time with demand."""
        if last_flame_off_at is None or last_update_at is None:
            return False

        if state.flame_active:
            return False

        if not DeviceStatusEvaluator.has_demand(state):
            return False

        time_since_off = last_update_at - last_flame_off_at
        if time_since_off < 0:
            return False

        return time_since_off < BOILER_ANTI_CYCLING_MIN_OFF_SECONDS

    @staticmethod
    def is_in_post_cycle_settling(state: DeviceState, last_update_at: Optional[float], last_flame_off_at: Optional[float]) -> bool:
        """Return True during a short settling period when there is no demand."""
        if last_flame_off_at is None or last_update_at is None:
            return False

        if DeviceStatusEvaluator.has_demand(state):
            return False

        time_since_off = last_update_at - last_flame_off_at
        return 0.0 <= time_since_off <= BOILER_POST_CYCLE_SETTLING_SECONDS

    @staticmethod
    def is_overshoot_cooling(state: DeviceState, last_flame_off_was_overshoot: bool) -> bool:
        """Return True when overshoot cooling keeps the flame off above setpoint."""
        if not last_flame_off_was_overshoot:
            return False

        if state.setpoint is None or state.flow_temperature is None:
            return False

        return (not state.flame_active) and state.flow_temperature > state.setpoint

    @staticmethod
    def is_pump_start_phase(state: DeviceState, previous: Optional[DeviceState], last_flame_on_at: Optional[float]) -> bool:
        """Detect the initial pump-start phase when the system is newly active."""
        # Once we have had a flame in this active session, we no longer call it pump start.
        if last_flame_on_at is not None:
            return False

        if state.setpoint is None or state.flow_temperature is None:
            return False

        if previous is None or previous.flow_temperature is None:
            return False

        # We only consider pump starting when we are clearly in preheat territory.
        delta_to_setpoint = state.setpoint - state.flow_temperature
        if delta_to_setpoint <= BOILER_PREHEAT_DELTA:
            return False

        # Pump circulating colder system water: flow temperature falling or flat.
        return state.flow_temperature - previous.flow_temperature <= 0.0

    @staticmethod
    def is_ramping_up(state: DeviceState, previous: Optional[DeviceState], last_flame_on_at: Optional[float], last_update_at: Optional[float], previous_update_at: Optional[float]) -> bool:
        """Detect rapid temperature rise shortly after flame-on."""
        if previous is None:
            return False

        if state.flow_temperature is None or previous.flow_temperature is None:
            return False

        if last_flame_on_at is None or last_update_at is None or previous_update_at is None:
            return False

        if last_update_at < previous_update_at:
            return False

        if last_update_at - last_flame_on_at > BOILER_RAMP_UP_WINDOW_SECONDS:
            return False

        delta_seconds = last_update_at - previous_update_at
        if delta_seconds <= 0:
            return False

        delta_temperature = state.flow_temperature - previous.flow_temperature
        if delta_temperature <= 0:
            return False

        ramp_rate = delta_temperature / delta_seconds
        return ramp_rate >= BOILER_RAMP_UP_RATE_CELSIUS_PER_SECOND
