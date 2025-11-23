from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import BoilerStatus, CycleClassification
from .cycles import Cycle

STORAGE_VERSION = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class BoilerState:
    is_active: bool
    is_inactive: bool

    flame_active: bool
    hot_water_active: bool

    setpoint: Optional[float]
    flow_temperature: Optional[float]
    return_temperature: Optional[float]
    relative_modulation_level: Optional[float]


class Boiler:
    def __init__(
            self,
            preheat_delta: float = 6.0,
            setpoint_band: float = 1.5,
            overshoot_delta: float = 2.0,

            demand_hysteresis: float = 0.7,
            anti_cycling_min_off_seconds: float = 180.0,

            gradient_threshold_up: float = 0.2,
            gradient_threshold_down: float = -0.1,

            pump_start_window_seconds: float = 20.0,
            post_cycle_settling_seconds: float = 60.0,

            modulation_delta_threshold: float = 3.0,
            modulation_reliability_min_samples: int = 8,
    ) -> None:
        # Configuration
        self._preheat_delta = preheat_delta
        self._setpoint_band = setpoint_band
        self._overshoot_delta = overshoot_delta
        self._anti_cycling_min_off_seconds = anti_cycling_min_off_seconds
        self._demand_hysteresis = demand_hysteresis
        self._gradient_threshold_up = gradient_threshold_up
        self._gradient_threshold_down = gradient_threshold_down
        self._pump_start_window_seconds = pump_start_window_seconds
        self._post_cycle_settling_seconds = post_cycle_settling_seconds
        self._modulation_delta_threshold = modulation_delta_threshold
        self._modulation_reliability_min_samples = modulation_reliability_min_samples

        # State
        self._last_cycle: Optional[Cycle] = None
        self._current_state: Optional[BoilerState] = None
        self._previous_state: Optional[BoilerState] = None
        self._current_status: Optional[BoilerStatus] = None

        self._last_update_at: Optional[float] = None
        self._last_flame_on_at: Optional[float] = None
        self._last_flame_off_at: Optional[float] = None
        self._last_flame_off_was_overshoot: bool = False

        # Modulation reliability tracking
        self._modulation_reliable: bool = True
        self._modulation_values_when_flame_on: List[float] = []

        self._store: Optional[Store] = None

    @property
    def status(self) -> BoilerStatus:
        if self._current_status is None:
            return BoilerStatus.INSUFFICIENT_DATA

        return self._current_status

    @property
    def current_state(self) -> Optional[BoilerState]:
        return self._current_state

    @property
    def previous_state(self) -> Optional[BoilerState]:
        return self._previous_state

    @property
    def modulation_reliable(self) -> bool:
        return self._modulation_reliable

    async def async_added_to_hass(self, hass: HomeAssistant, device_id: str) -> None:
        if self._store is None:
            self._store = Store(hass, STORAGE_VERSION, f"sat.boiler.{device_id}")

        data = await self._store.async_load() or {}
        stored_flag = data.get("modulation_reliable")
        if stored_flag is not None:
            self._modulation_reliable = bool(stored_flag)

    async def async_will_remove_from_hass(self) -> None:
        if self._store is None:
            return

        await self._store.async_save({"modulation_reliable": self._modulation_reliable})

    def update(self, state: BoilerState, last_cycle: Optional[Cycle], timestamp: Optional[float] = None):
        if timestamp is None:
            timestamp = time.monotonic()

        previous = self._current_state

        self._current_state = state
        self._last_cycle = last_cycle
        self._previous_state = previous
        self._last_update_at = timestamp

        self._track_flame_transitions(previous, state, timestamp)
        self._update_modulation_reliability(state)

        self._current_status = self._derive_status(timestamp)

    def _derive_status(self, now: float) -> BoilerStatus:
        state = self._current_state
        previous = self._previous_state

        if state is None:
            # Should not happen in normal usage; treat as inactive.
            return BoilerStatus.OFF

        # Power / availability
        if not state.is_active or state.is_inactive:
            return BoilerStatus.OFF

        # The previous cycle ended in overshoot short-cycling → apply protection
        if self._last_cycle is not None and self._last_cycle.classification == CycleClassification.SHORT_CYCLING_OVERSHOOT:
            return BoilerStatus.SHORT_CYCLING

        # Transitional and timing logic when flame is OFF
        if not state.flame_active:
            # Overshoot cooling: flame off due to overshoot, still above setpoint.
            if self._is_in_overshoot_cooling(state):
                return BoilerStatus.OVERSHOOT_COOLING

            # Anti-cycling: off despite demand, within minimum off time.
            if self._is_anti_cycling(state, now):
                return BoilerStatus.ANTI_CYCLING

            # Flame just turned off, and we are not overshoot cooling nor anti cycling.
            if previous is not None and previous.flame_active:
                return BoilerStatus.COOLING

            # Just became active → pump starting phase.
            if self._is_pump_starting(state):
                return BoilerStatus.PUMP_STARTING

            # Waiting for flame: active, demand present, not anti cycling.
            if self._demand_present(state):
                return BoilerStatus.WAITING_FOR_FLAME

            # Post-cycle settling: shortly after last off, no demand yet.
            if self._is_post_cycle_settling(state, now):
                return BoilerStatus.POST_CYCLE_SETTLING

            # Otherwise, simply idle.
            return BoilerStatus.IDLE

        # From here on: flame is ON
        if state.hot_water_active:
            # Note: DHW_COMFORT_PREHEAT usually needs extra signals.
            # For now, all DHW burns are treated as HEATING_HOT_WATER.
            return BoilerStatus.HEATING_HOT_WATER

        # Space heating with flame on.
        if state.setpoint is None or state.flow_temperature is None:
            # Without temperatures, we cannot distinguish phases well.
            return BoilerStatus.CENTRAL_HEATING

        delta_to_setpoint = state.setpoint - state.flow_temperature

        # Preheating: far below the setpoint.
        if delta_to_setpoint > self._preheat_delta:
            return BoilerStatus.PREHEATING

        # At-setpoint band: very close to setpoint.
        if abs(delta_to_setpoint) <= self._setpoint_band:
            return BoilerStatus.AT_SETPOINT_BAND

        # Otherwise: direction of modulation (up/down) based on modulation or gradients.
        modulation_direction = self._modulation_direction()

        if modulation_direction > 0:
            return BoilerStatus.MODULATING_UP
        if modulation_direction < 0:
            return BoilerStatus.MODULATING_DOWN

        # Fallback: generic heating space.
        return BoilerStatus.CENTRAL_HEATING

    def _demand_present(self, state: BoilerState) -> bool:
        if state.setpoint is None or state.flow_temperature is None:
            return False

        return state.setpoint > state.flow_temperature + self._demand_hysteresis

    def _is_pump_starting(self, state: BoilerState) -> bool:
        # Once we have had a flame in this active session, we no longer call it pump start.
        if self._last_flame_on_at is not None:
            return False

        if state.setpoint is None or state.flow_temperature is None:
            return False

        previous = self._previous_state
        if previous is None or previous.flow_temperature is None:
            return False

        # We only consider pump starting when we are clearly in preheat territory.
        delta_to_setpoint = state.setpoint - state.flow_temperature

        if delta_to_setpoint <= self._preheat_delta:
            return False

        # Pump circulating colder system water: flow temperature falling or flat.
        return state.flow_temperature - previous.flow_temperature <= 0.0

    def _is_post_cycle_settling(self, state: BoilerState, now: float) -> bool:
        if self._last_flame_off_at is None:
            return False

        if self._demand_present(state):
            return False

        time_since_off = now - self._last_flame_off_at
        return 0.0 <= time_since_off <= self._post_cycle_settling_seconds

    def _is_in_overshoot_cooling(self, state: BoilerState) -> bool:
        if not self._last_flame_off_was_overshoot:
            return False

        if state.setpoint is None or state.flow_temperature is None:
            return False

        return not state.flame_active and state.flow_temperature > state.setpoint

    def _is_anti_cycling(self, state: BoilerState, now: float) -> bool:
        if self._last_flame_off_at is None:
            return False

        if state.flame_active:
            return False

        if not self._demand_present(state):
            return False

        time_since_off = now - self._last_flame_off_at
        if time_since_off < 0:
            return False

        return time_since_off < self._anti_cycling_min_off_seconds

    def _track_flame_transitions(self, previous: Optional[BoilerState], current: BoilerState, now: float, ) -> None:
        if previous is None:
            if current.flame_active:
                self._last_flame_on_at = now
            return

        if previous.flame_active and not current.flame_active:
            # Flame ON -> OFF
            self._last_flame_off_at = now
            self._last_flame_off_was_overshoot = self._is_overshoot_at_flame_off(previous)

        elif not previous.flame_active and current.flame_active:
            # Flame OFF -> ON
            self._last_flame_on_at = now
            self._last_flame_off_was_overshoot = False

    def _is_overshoot_at_flame_off(self, state: BoilerState) -> bool:
        """ Use the state at the moment of flame-off to decide if we shut down because of an overshoot."""
        if state.setpoint is None or state.flow_temperature is None:
            return False

        return state.flow_temperature >= state.setpoint + self._overshoot_delta

    def _update_modulation_reliability(self, state: BoilerState) -> None:
        """Detect boilers that always report relative_modulation_level as zero (or effectively constant) while the flame is on."""
        if not state.flame_active:
            return

        value = state.relative_modulation_level
        if value is None:
            return

        self._modulation_values_when_flame_on.append(value)
        if len(self._modulation_values_when_flame_on) > 50:
            self._modulation_values_when_flame_on = self._modulation_values_when_flame_on[-50:]

        if not self._modulation_reliable:
            return

        if len(self._modulation_values_when_flame_on) < self._modulation_reliability_min_samples:
            return

        values = self._modulation_values_when_flame_on
        max_value = max(values)
        min_value = min(values)

        if max_value - min_value < 1e-3 and abs(max_value) < 1e-3:
            # Modulation is effectively stuck at ~0 while the flame is on.
            self._modulation_reliable = False

    def _modulation_direction(self) -> int:
        """Determine modulation direction."""
        current = self._current_state
        previous = self._previous_state

        if current is None or previous is None:
            return 0

        # Prefer the reliable modulation level if available.
        if self._modulation_reliable:
            cur_mod = current.relative_modulation_level
            prev_mod = previous.relative_modulation_level
            if cur_mod is not None and prev_mod is not None:
                delta_mod = cur_mod - prev_mod
                if delta_mod > self._modulation_delta_threshold:
                    return 1

                if delta_mod < -self._modulation_delta_threshold:
                    return -1

        # Fallback: temperature gradient.
        if current.flow_temperature is None or previous.flow_temperature is None:
            return 0

        delta_flow = current.flow_temperature - previous.flow_temperature

        if delta_flow > self._gradient_threshold_up:
            return 1

        if delta_flow < self._gradient_threshold_down:
            return -1

        return 0
