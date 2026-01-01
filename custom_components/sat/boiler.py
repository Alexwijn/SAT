from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import UNHEALTHY_CYCLES
from .types import BoilerStatus

if TYPE_CHECKING:
    from .cycles import Cycle

STORAGE_VERSION = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class BoilerControlIntent:
    setpoint: Optional[float]
    relative_modulation: Optional[float]


@dataclass(frozen=True, slots=True, kw_only=True)
class BoilerCapabilities:
    # Setpoint limits
    minimum_setpoint: float
    maximum_setpoint: float


@dataclass(frozen=True, slots=True, kw_only=True)
class BoilerState:
    # Activity state
    flame_active: bool
    central_heating: bool
    hot_water_active: bool
    modulation_reliable: bool

    # Flame timing
    flame_on_since: Optional[float]
    flame_off_since: Optional[float]

    # Temperatures / modulation
    setpoint: Optional[float]
    flow_temperature: Optional[float]
    return_temperature: Optional[float]
    relative_modulation_level: Optional[float]

    @property
    def flow_setpoint_error(self) -> Optional[float]:
        return self.flow_temperature - self.setpoint if self.flow_temperature is not None and self.setpoint is not None else None

    @property
    def flow_return_delta(self) -> Optional[float]:
        return self.flow_temperature - self.return_temperature if self.flow_temperature is not None and self.return_temperature is not None else None


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

            stall_ignition_off_ratio: float = 3.0,
            stall_ignition_min_off_seconds: float = 600.0,
    ) -> None:
        # Configuration
        self._preheat_delta = preheat_delta
        self._setpoint_band = setpoint_band
        self._overshoot_delta = overshoot_delta

        self._demand_hysteresis = demand_hysteresis
        self._anti_cycling_min_off_seconds = anti_cycling_min_off_seconds

        self._gradient_threshold_up = gradient_threshold_up
        self._gradient_threshold_down = gradient_threshold_down

        self._pump_start_window_seconds = pump_start_window_seconds
        self._post_cycle_settling_seconds = post_cycle_settling_seconds

        self._modulation_delta_threshold = modulation_delta_threshold
        self._modulation_reliability_min_samples = modulation_reliability_min_samples

        self._stall_ignition_off_ratio = stall_ignition_off_ratio
        self._stall_ignition_min_off_seconds = stall_ignition_min_off_seconds

        # State
        self._last_cycle: Optional["Cycle"] = None
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

        # Persistence for modulation reliability
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

    @property
    def flame_on_since(self) -> Optional[int]:
        return self._last_flame_on_at

    @property
    def flame_off_since(self) -> Optional[int]:
        return self._last_flame_off_at

    async def async_added_to_hass(self, hass: HomeAssistant, device_id: str) -> None:
        """Called when entity is added to Home Assistant, restore persisted flags."""
        if self._store is None:
            self._store = Store(hass, STORAGE_VERSION, f"sat.boiler.{device_id}")

        data = await self._store.async_load() or {}
        stored_flag = data.get("modulation_reliable")
        if stored_flag is not None:
            self._modulation_reliable = bool(stored_flag)

        async_track_time_interval(hass, self.async_save_options, timedelta(minutes=15))

    async def async_save_options(self, _time: Optional[datetime] = None) -> None:
        """Persist modulation reliability on removal."""
        if self._store is None:
            return

        await self._store.async_save({"modulation_reliable": self._modulation_reliable})

    def update(self, state: "BoilerState", last_cycle: Optional["Cycle"], timestamp: Optional[float] = None) -> None:
        """Update boiler classification with the latest state and last cycle summary."""
        timestamp = timestamp or time.monotonic()
        previous = self._current_state

        self._current_state = state
        self._last_cycle = last_cycle
        self._previous_state = previous
        self._last_update_at = timestamp

        if not self._demand_present(state):
            self._last_flame_off_at = None

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
        if not state.central_heating:
            return BoilerStatus.OFF

        if not state.flame_active:
            # Overshoot cooling: flame off due to overshoot, still above setpoint.
            if self._is_in_overshoot_cooling(state):
                return BoilerStatus.OVERSHOOT_COOLING

            # Anti-cycling: off despite demand, within minimum off time.
            if self._is_anti_cycling(state, now):
                return BoilerStatus.ANTI_CYCLING

            # Stalled ignition: OFF for much longer than expected, with demand present.
            if self._is_stalled_ignition(state, now):
                return BoilerStatus.STALLED_IGNITION

            # Flame just turned off, and we are not overshoot cooling nor anti cycling.
            if previous is not None and previous.flame_active:
                return BoilerStatus.COOLING

            # Just became active → pump starting phase.
            if self._is_pump_starting(state):
                return BoilerStatus.PUMP_STARTING

            # Waiting for flame: active, demand present, not anti cycling, not stalled.
            if self._demand_present(state):
                return BoilerStatus.WAITING_FOR_FLAME

            # Post-cycle settling: shortly after last off, no demand yet.
            if self._is_post_cycle_settling(state, now):
                return BoilerStatus.POST_CYCLE_SETTLING

            if self._last_cycle is not None and self._last_cycle.classification in UNHEALTHY_CYCLES:
                return BoilerStatus.SHORT_CYCLING

            # Otherwise, simply idle.
            return BoilerStatus.IDLE

        if state.hot_water_active:
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
        """Return True if space-heating demand is present."""
        if state.setpoint is None or state.flow_temperature is None:
            return False

        return state.setpoint > state.flow_temperature + self._demand_hysteresis

    def _is_pump_starting(self, state: BoilerState) -> bool:
        """Detect the initial pump-start phase when the system is newly active."""
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
        """Short settling period after a cycle when there is no demand."""
        if self._last_flame_off_at is None:
            return False

        if self._demand_present(state):
            return False

        time_since_off = now - self._last_flame_off_at
        return 0.0 <= time_since_off <= self._post_cycle_settling_seconds

    def _is_in_overshoot_cooling(self, state: BoilerState) -> bool:
        """True when we turned off due to overshoot and are still above setpoint."""
        if not self._last_flame_off_was_overshoot:
            return False

        if state.setpoint is None or state.flow_temperature is None:
            return False

        return (not state.flame_active) and state.flow_temperature > state.setpoint

    def _is_anti_cycling(self, state: BoilerState, now: float) -> bool:
        """True when boiler is in enforced anti-cycling off-time with demand."""
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

    def _is_stalled_ignition(self, state: BoilerState, now: float) -> bool:
        """Detect "stalled ignition": flame has stayed OFF much longer than expected while there is clear heating demand."""
        if self._last_flame_off_at is None:
            return False

        if state.flame_active:
            return False

        if not self._demand_present(state):
            return False

        # If we are still in anti-cycling or overshoot cooling, do not call this stalled.
        if self._is_anti_cycling(state, now):
            return False

        if self._is_in_overshoot_cooling(state):
            return False

        time_since_off = now - self._last_flame_off_at
        if time_since_off < 0:
            return False

        # Base threshold on the last cycle duration (if available) plus an absolute floor.
        threshold = self._stall_ignition_min_off_seconds

        if self._last_cycle is not None:
            try:
                last_duration = float(self._last_cycle.duration)
                threshold = max(threshold, last_duration * self._stall_ignition_off_ratio)
            except (TypeError, ValueError):
                # Ignore if duration_seconds is missing or not numeric.
                pass

        return time_since_off >= threshold

    def _track_flame_transitions(self, previous: Optional[BoilerState], current: BoilerState, now: float, ) -> None:
        """Track flame ON/OFF timestamps and overshoot at OFF."""
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
        """Use the state at the moment of flame-off to decide if we shut down because of an overshoot."""
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
        """Determine modulation direction"""
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
