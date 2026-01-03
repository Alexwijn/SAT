import logging
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING, Dict, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import UNHEALTHY_CYCLES
from .helpers import timestamp
from .types import BoilerStatus

if TYPE_CHECKING:
    from .cycles import Cycle

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Boiler behavior thresholds.
BOILER_PREHEAT_DELTA = 6.0
BOILER_SETPOINT_BAND = 1.5
BOILER_OVERSHOOT_DELTA = 2.0
BOILER_DEMAND_HYSTERESIS = 0.7
BOILER_ANTI_CYCLING_MIN_OFF_SECONDS = 180.0
BOILER_GRADIENT_THRESHOLD_UP = 0.2
BOILER_GRADIENT_THRESHOLD_DOWN = -0.1
BOILER_POST_CYCLE_SETTLING_SECONDS = 60.0
BOILER_MODULATION_DELTA_THRESHOLD = 3.0
BOILER_MODULATION_RELIABILITY_MIN_SAMPLES = 8
BOILER_STALL_IGNITION_OFF_RATIO = 3.0
BOILER_STALL_IGNITION_MIN_OFF_SECONDS = 600.0


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
    modulation_reliable: Optional[bool]

    # Flame timing
    flame_on_since: Optional[float]
    flame_off_since: Optional[float]

    # Temperatures / modulation
    setpoint: Optional[float]
    flow_temperature: Optional[float]
    return_temperature: Optional[float]
    max_modulation_level: Optional[float]
    relative_modulation_level: Optional[float]

    @property
    def flow_setpoint_error(self) -> Optional[float]:
        return self.flow_temperature - self.setpoint if self.flow_temperature is not None and self.setpoint is not None else None

    @property
    def flow_return_delta(self) -> Optional[float]:
        return self.flow_temperature - self.return_temperature if self.flow_temperature is not None and self.return_temperature is not None else None


class Boiler:
    def __init__(self) -> None:
        # Runtime state
        self._last_cycle: Optional["Cycle"] = None
        self._current_state: Optional[BoilerState] = None
        self._previous_state: Optional[BoilerState] = None
        self._current_status: Optional[BoilerStatus] = None

        self._last_update_at: Optional[float] = None
        self._last_flame_on_at: Optional[float] = None
        self._last_flame_off_at: Optional[float] = None
        self._last_flame_off_was_overshoot: bool = False

        # Modulation reliability tracking
        self._modulation_reliable: Optional[bool] = None
        self._modulation_values_when_flame_on: List[float] = []

        # Persistence for modulation reliability
        self._store: Optional[Store] = None
        self._hass: Optional[HomeAssistant] = None

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
    def modulation_reliable(self) -> Optional[bool]:
        return self._modulation_reliable

    @property
    def flame_on_since(self) -> Optional[int]:
        return self._last_flame_on_at

    @property
    def flame_off_since(self) -> Optional[int]:
        return self._last_flame_off_at

    async def async_added_to_hass(self, hass: HomeAssistant, device_id: str) -> None:
        """Restore boiler state from storage when the integration loads."""
        self._hass = hass

        if self._store is None:
            self._store = Store(hass, STORAGE_VERSION, f"sat.boiler.{device_id}")

        data: Optional[Dict[str, Any]] = await self._store.async_load()
        if not data:
            return

        try:
            modulation_reliable = data["modulation_reliable"]
        except (KeyError, TypeError, ValueError):
            return

        self._modulation_reliable = modulation_reliable

        _LOGGER.debug("Loaded boiler state from storage (modulation_reliable=%s).", modulation_reliable)

    async def async_save_data(self) -> None:
        if self._store is None:
            return

        await self._store.async_save({"modulation_reliable": self._modulation_reliable})
        _LOGGER.debug("Saved boiler state to storage (modulation_reliable=%s).", self._modulation_reliable)

    def update(self, state: "BoilerState", last_cycle: Optional["Cycle"]) -> None:
        """Update the internal state and derive the current boiler status."""
        previous = self._current_state
        self._current_state = state
        self._last_cycle = last_cycle
        self._previous_state = previous
        self._last_update_at = timestamp()

        if not self._has_demand(state):
            self._last_flame_off_at = None

        self._record_flame_transitions(previous, state)
        self._update_modulation_reliability(state)

        self._current_status = self._determine_status()

    def _determine_status(self) -> BoilerStatus:
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
            if self._is_overshoot_cooling(state, self._last_flame_off_was_overshoot):
                return BoilerStatus.OVERSHOOT_COOLING

            # Anti-cycling: off despite demand, within minimum off time.
            if self._is_in_anti_cycling(state, self._last_update_at, self._last_flame_off_at):
                return BoilerStatus.ANTI_CYCLING

            # Stalled ignition: OFF for much longer than expected, with demand present.
            if self._is_ignition_stalled(
                    state,
                    self._last_update_at,
                    self._last_flame_off_at,
                    self._last_flame_off_was_overshoot,
                    self._last_cycle,
            ):
                return BoilerStatus.STALLED_IGNITION

            # Flame just turned off, and we are not overshoot cooling nor anti cycling.
            if previous is not None and previous.flame_active:
                return BoilerStatus.COOLING

            # Just became active → pump starting phase.
            if self._is_pump_start_phase(state, self._previous_state, self._last_flame_on_at):
                return BoilerStatus.PUMP_STARTING

            if self._last_cycle is not None and self._last_cycle.classification in UNHEALTHY_CYCLES:
                return BoilerStatus.SHORT_CYCLING

            # Waiting for flame: active, demand present, not anti cycling, not stalled.
            if self._has_demand(state):
                return BoilerStatus.WAITING_FOR_FLAME

            # Post-cycle settling: shortly after last off, no demand yet.
            if self._is_in_post_cycle_settling(state, self._last_update_at, self._last_flame_off_at):
                return BoilerStatus.POST_CYCLE_SETTLING

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
        if delta_to_setpoint > BOILER_PREHEAT_DELTA:
            return BoilerStatus.PREHEATING

        # At-setpoint band: very close to setpoint.
        if abs(delta_to_setpoint) <= BOILER_SETPOINT_BAND:
            return BoilerStatus.AT_SETPOINT_BAND

        # Otherwise: direction of modulation (up/down) based on modulation or gradients.
        modulation_direction = self._determine_modulation_direction()

        if modulation_direction > 0:
            return BoilerStatus.MODULATING_UP

        if modulation_direction < 0:
            return BoilerStatus.MODULATING_DOWN

        # Fallback: generic heating space.
        return BoilerStatus.CENTRAL_HEATING

    def _update_modulation_reliability(self, state: BoilerState) -> None:
        """Track whether modulation readings show sustained, meaningful variation."""
        if not state.flame_active:
            return

        max_modulation = state.max_modulation_level
        current_modulation = state.relative_modulation_level
        if current_modulation is None or max_modulation < BOILER_MODULATION_DELTA_THRESHOLD:
            return

        self._modulation_values_when_flame_on.append(current_modulation)

        if len(self._modulation_values_when_flame_on) > 50:
            self._modulation_values_when_flame_on = self._modulation_values_when_flame_on[-50:]

        if len(self._modulation_values_when_flame_on) < BOILER_MODULATION_RELIABILITY_MIN_SAMPLES:
            return

        window = self._modulation_values_when_flame_on[-BOILER_MODULATION_RELIABILITY_MIN_SAMPLES:]
        above_threshold = sum(1 for value in window if value >= BOILER_MODULATION_DELTA_THRESHOLD)
        required_samples = max(2, int(len(window) * 0.4))

        self._modulation_reliable = above_threshold >= required_samples

        if self._hass is not None:
            self._hass.create_task(self.async_save_data())

    def _determine_modulation_direction(self) -> int:
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
                if delta_mod > BOILER_MODULATION_DELTA_THRESHOLD:
                    return 1

                if delta_mod < -BOILER_MODULATION_DELTA_THRESHOLD:
                    return -1

        # Fallback: temperature gradient.
        if current.flow_temperature is None or previous.flow_temperature is None:
            return 0

        delta_flow = current.flow_temperature - previous.flow_temperature

        if delta_flow > BOILER_GRADIENT_THRESHOLD_UP:
            return 1

        if delta_flow < BOILER_GRADIENT_THRESHOLD_DOWN:
            return -1

        return 0

    def _record_flame_transitions(self, previous: Optional[BoilerState], current: BoilerState) -> None:
        """Track flame ON/OFF timestamps and overshoot at OFF."""
        if previous is None:
            if current.flame_active:
                self._last_flame_on_at = self._last_update_at
            return

        if previous.flame_active and not current.flame_active:
            # Flame ON -> OFF
            self._last_flame_off_at = self._last_update_at
            self._last_flame_off_was_overshoot = self._did_overshoot_at_flame_off(previous)

        elif not previous.flame_active and current.flame_active:
            # Flame OFF -> ON
            self._last_flame_on_at = self._last_update_at
            self._last_flame_off_was_overshoot = False

    @staticmethod
    def _is_pump_start_phase(state: BoilerState, previous: Optional[BoilerState], last_flame_on_at: Optional[float]) -> bool:
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
    def _is_in_post_cycle_settling(state: BoilerState, last_update_at: Optional[float], last_flame_off_at: Optional[float]) -> bool:
        """Return True during a short settling period when there is no demand."""
        if last_flame_off_at is None or last_update_at is None:
            return False

        if Boiler._has_demand(state):
            return False

        time_since_off = last_update_at - last_flame_off_at
        return 0.0 <= time_since_off <= BOILER_POST_CYCLE_SETTLING_SECONDS

    @staticmethod
    def _is_overshoot_cooling(state: BoilerState, last_flame_off_was_overshoot: bool) -> bool:
        """Return True when overshoot cooling keeps the flame off above setpoint."""
        if not last_flame_off_was_overshoot:
            return False

        if state.setpoint is None or state.flow_temperature is None:
            return False

        return (not state.flame_active) and state.flow_temperature > state.setpoint

    @staticmethod
    def _is_in_anti_cycling(state: BoilerState, last_update_at: Optional[float], last_flame_off_at: Optional[float]) -> bool:
        """Return True when boiler is in enforced anti-cycling off-time with demand."""
        if last_flame_off_at is None or last_update_at is None:
            return False

        if state.flame_active:
            return False

        if not Boiler._has_demand(state):
            return False

        time_since_off = last_update_at - last_flame_off_at
        if time_since_off < 0:
            return False

        return time_since_off < BOILER_ANTI_CYCLING_MIN_OFF_SECONDS

    @staticmethod
    def _is_ignition_stalled(state: BoilerState, last_update_at: Optional[float], last_flame_off_at: Optional[float], last_flame_off_was_overshoot: bool, last_cycle: Optional["Cycle"]) -> bool:
        """Detect stalled ignition when demand persists for too long."""
        if last_flame_off_at is None or last_update_at is None:
            return False

        if state.flame_active:
            return False

        if not Boiler._has_demand(state):
            return False

        # If we are still in anti-cycling or overshoot cooling, do not call this stalled.
        if Boiler._is_in_anti_cycling(state, last_update_at, last_flame_off_at):
            return False

        if Boiler._is_overshoot_cooling(state, last_flame_off_was_overshoot):
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
    def _has_demand(state: BoilerState) -> bool:
        """Return True if space-heating demand is present."""
        if state.setpoint is None or state.flow_temperature is None:
            return False

        return state.setpoint > state.flow_temperature + BOILER_DEMAND_HYSTERESIS

    @staticmethod
    def _did_overshoot_at_flame_off(state: BoilerState) -> bool:
        """Return True if the flame turned off because of overshoot."""
        if state.setpoint is None or state.flow_temperature is None:
            return False

        return state.flow_temperature >= state.setpoint + BOILER_OVERSHOOT_DELTA
