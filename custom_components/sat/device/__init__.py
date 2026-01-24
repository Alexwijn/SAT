from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING, Dict, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import *
from .modulation import ModulationReliabilityTracker
from .status import DeviceStatusEvaluator, DeviceStatusSnapshot
from .types import DeviceCapabilities, DeviceState
from ..types import BoilerStatus

if TYPE_CHECKING:
    from ..cycles import Cycle

_LOGGER = logging.getLogger(__name__)


class DeviceTracker:
    def __init__(self) -> None:
        # Runtime state
        self._last_cycle: Optional["Cycle"] = None
        self._current_state: Optional[DeviceState] = None
        self._previous_state: Optional[DeviceState] = None
        self._current_status: Optional[BoilerStatus] = None

        self._last_update_at: Optional[float] = None
        self._previous_update_at: Optional[float] = None

        self._last_flame_on_at: Optional[float] = None
        self._last_flame_off_at: Optional[float] = None
        self._last_flame_off_was_overshoot: bool = False

        self._modulation_tracker = ModulationReliabilityTracker()

        # Persistence for modulation reliability
        self._store: Optional[Store] = None
        self._hass: Optional[HomeAssistant] = None

    @property
    def status(self) -> BoilerStatus:
        if self._current_status is None:
            return BoilerStatus.INSUFFICIENT_DATA

        return self._current_status

    @property
    def current_state(self) -> Optional[DeviceState]:
        return self._current_state

    @property
    def previous_state(self) -> Optional[DeviceState]:
        return self._previous_state

    @property
    def modulation_reliable(self) -> Optional[bool]:
        return self._modulation_tracker.reliable

    @property
    def flame_on_since(self) -> Optional[int]:
        return self._last_flame_on_at

    @property
    def flame_off_since(self) -> Optional[int]:
        return self._last_flame_off_at

    async def async_added_to_hass(self, hass: HomeAssistant, device_id: str) -> None:
        """Restore device state from storage when the integration loads."""
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, f"sat.device.{device_id}")

        data: Optional[Dict[str, Any]] = await self._store.async_load()
        if not data:
            legacy_store = Store(hass, STORAGE_VERSION, f"sat.boiler.{device_id}")

            if data := await legacy_store.async_load():
                await self._store.async_save(data)
                _LOGGER.debug("Migrated legacy boiler storage to device storage.")

        if not data:
            return

        try:
            modulation_reliable = data["modulation_reliable"]
        except (KeyError, TypeError, ValueError):
            return

        self._modulation_tracker.load(modulation_reliable)

        _LOGGER.debug("Loaded device state from storage (modulation_reliable=%s).", modulation_reliable)

    async def async_save_data(self) -> None:
        if self._store is None:
            return

        await self._store.async_save({"modulation_reliable": self._modulation_tracker.reliable})
        _LOGGER.debug("Saved device state to storage (modulation_reliable=%s).", self._modulation_tracker.reliable)

    def update(self, state: DeviceState, last_cycle: Optional["Cycle"], timestamp: float) -> None:
        """Update the internal state and derive the current device status."""
        self._last_cycle = last_cycle

        self._previous_state = self._current_state
        self._current_state = state

        self._previous_update_at = self._last_update_at
        self._last_update_at = timestamp

        if not DeviceStatusEvaluator.has_demand(state):
            self._last_flame_off_at = None

        self._record_flame_transitions(self._previous_state, state)

        if self._modulation_tracker.update(state) and self._hass is not None:
            self._hass.create_task(self.async_save_data())

        self._current_status = self._determine_status()

    def _determine_status(self) -> BoilerStatus:
        state = self._current_state
        previous = self._previous_state

        if state is None:
            # Should not happen in normal usage; treat as inactive.
            return BoilerStatus.OFF

        return DeviceStatusEvaluator.evaluate(DeviceStatusSnapshot(
            state=state,
            previous_state=previous,
            previous_update_at=self._previous_update_at,
            last_flame_off_was_overshoot=self._last_flame_off_was_overshoot,

            last_cycle=self._last_cycle,
            last_flame_on_at=self._last_flame_on_at,
            last_flame_off_at=self._last_flame_off_at,
            last_update_at=self._last_update_at,

            modulation_direction=self._determine_modulation_direction(),
        ))

    def _determine_modulation_direction(self) -> int:
        """Determine modulation direction."""
        current = self._current_state
        previous = self._previous_state

        if current is None or previous is None:
            return 0

        # Prefer the reliable modulation level if available.
        if self._modulation_tracker.reliable:
            cur_mod = current.relative_modulation_level
            prev_mod = previous.relative_modulation_level
            if cur_mod is not None and prev_mod is not None:
                delta_mod = cur_mod - prev_mod
                if delta_mod > BOILER_MODULATION_DELTA_THRESHOLD:
                    return +1

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

    def _record_flame_transitions(self, previous: Optional[DeviceState], current: DeviceState) -> None:
        """Track flame ON/OFF timestamps and overshoot at OFF."""
        if previous is None:
            if current.flame_active:
                self._last_flame_on_at = self._last_update_at

            return

        if previous.flame_active and not current.flame_active:
            # Flame ON -> OFF
            self._last_flame_off_at = self._last_update_at
            self._last_flame_off_was_overshoot = DeviceStatusEvaluator.did_overshoot_at_flame_off(previous)

        elif not previous.flame_active and current.flame_active:
            # Flame OFF -> ON
            self._last_flame_on_at = self._last_update_at
            self._last_flame_off_was_overshoot = False


__all__ = [
    "DeviceTracker",
    "DeviceState",
    "DeviceCapabilities",
]
