from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import *
from .regimes import RegimeBucketizer, RegimeKey, RegimeState
from .types import RegimeSample
from .seeding import RegimeSeeder
from .tuner import MinimumSetpointTuner
from ..boiler import BoilerCapabilities, BoilerControlIntent
from ..coordinator import ControlLoopSample
from ..const import MINIMUM_SETPOINT
from ..helpers import clamp
from ..types import CycleClassification

if TYPE_CHECKING:
    from ..cycles import Cycle, CycleStatistics

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MinimumSetpointConfig:
    minimum_setpoint: float
    maximum_setpoint: float


class DynamicMinimumSetpoint:
    """Adaptive minimum setpoint learner with regime-based memory."""

    def __init__(self, config: MinimumSetpointConfig) -> None:
        self._config: MinimumSetpointConfig = config

        self._store: Optional[Store] = None
        self._value: Optional[float] = None
        self._hass: Optional[HomeAssistant] = None

        self._regimes: Dict[RegimeKey, RegimeState] = {}
        self._active_regime: Optional[RegimeState] = None

        self._bucketizer = RegimeBucketizer()
        self._setpoint_history: deque[float] = deque(maxlen=REGIME_SETPOINT_SMOOTHING_SAMPLES)

    @property
    def value(self) -> float:
        return round(self._value if self._value is not None else self._config.minimum_setpoint, 1)

    @property
    def active_regime(self) -> Optional[RegimeState]:
        """Return the currently active regime state, if any."""
        return self._active_regime

    @property
    def regime_count(self) -> int:
        """Return the number of known regimes."""
        return len(self._regimes)

    def reset(self) -> None:
        """Reset learned minimums and internal state."""
        self._value = None
        self._regimes.clear()
        self._active_regime = None

        self._bucketizer = RegimeBucketizer()
        self._setpoint_history.clear()

    def on_cycle_start(self, boiler_capabilities: BoilerCapabilities, sample: ControlLoopSample) -> None:
        """Initialize or switch regime buckets when a new cycle begins."""
        if sample.state.hot_water_active:
            return

        if sample.requested_setpoint is None and sample.intent.setpoint is not None and sample.intent.setpoint <= MINIMUM_SETPOINT:
            return

        if sample.intent.setpoint is None and sample.requested_setpoint is None:
            return

        active_key = self._make_regime_key(sample)
        now = datetime.now(timezone.utc)

        if (regime_state := self._regimes.get(active_key)) is None:
            regime_state = RegimeState(
                key=active_key,
                minimum_setpoint=self._seed_for_regime(
                    active_key=active_key,
                    boiler_control_intent=sample.intent,
                    boiler_capabilities=boiler_capabilities,
                ),
            )

            self._regimes[active_key] = regime_state
            _LOGGER.info(
                "Initialized regime %s at cycle start with minimum_setpoint=%.2f",
                active_key.to_storage(),
                regime_state.minimum_setpoint,
            )

        self._active_regime = regime_state
        regime_state.last_seen = now
        self._prune_regimes(now)

        self._value = regime_state.minimum_setpoint

    def on_cycle_end(self, boiler_capabilities: BoilerCapabilities, cycles: "CycleStatistics", cycle: "Cycle") -> None:
        """Update regime statistics and persist tuning decisions on cycle completion."""
        if (regime_state := self._active_regime) is None:
            return

        # Mark a cycle as completed.
        regime_state.completed_cycles += 1

        # Mark a cycle as stable when the classification is GOOD.
        if cycle.classification == CycleClassification.GOOD:
            regime_state.stable_cycles += 1

        _LOGGER.debug(
            "Cycle ended with %s (regime=%s, completed_cycles=%d, stable_cycles=%d)",
            cycle.classification.name,
            regime_state.key.to_storage(),
            regime_state.completed_cycles,
            regime_state.stable_cycles,
        )

        # Track before/after for tuning visibility
        previous_minimum = regime_state.minimum_setpoint
        MinimumSetpointTuner.tune(
            boiler_capabilities=boiler_capabilities,
            cycles=cycles,
            cycle=cycle,
            regime_state=regime_state,
        )

        # Clamp learned minimum for this regime to absolute range.
        regime_state.minimum_setpoint = clamp(
            regime_state.minimum_setpoint,
            boiler_capabilities.minimum_setpoint,
            boiler_capabilities.maximum_setpoint,
        )

        if regime_state.minimum_setpoint != previous_minimum:
            _LOGGER.info(
                "Regime %s minimum setpoint adjusted: %.2f → %.2f",
                regime_state.key.to_storage(),
                previous_minimum,
                regime_state.minimum_setpoint,
            )
        else:
            _LOGGER.debug(
                "Regime %s minimum setpoint unchanged at %.2f",
                regime_state.key.to_storage(),
                regime_state.minimum_setpoint,
            )

        self._value = regime_state.minimum_setpoint

        if self._hass is not None:
            self._hass.create_task(self.async_save_regimes())

    async def async_added_to_hass(self, hass: HomeAssistant, device_id: str) -> None:
        """Restore learned regimes from storage when the integration loads."""
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, f"sat.minimum_setpoint.{device_id}")

        data: Optional[Dict[str, Any]] = await self._store.async_load()
        if not data:
            return

        regimes_data = data.get(STORAGE_KEY_REGIMES, {})
        self._regimes.clear()
        now = datetime.now(timezone.utc)

        for key, item in regimes_data.items():
            if not isinstance(item, dict):
                continue

            try:
                minimum_setpoint = float(item[STORAGE_KEY_MINIMUM_SETPOINT])
            except (KeyError, TypeError, ValueError):
                continue

            try:
                completed = int(item.get(STORAGE_KEY_COMPLETED_CYCLES, 0))
            except (TypeError, ValueError):
                completed = 0

            try:
                stable = int(item.get(STORAGE_KEY_STABLE_CYCLES, 0))
            except (TypeError, ValueError):
                stable = 0

            parsed_key = RegimeKey.from_storage(str(key))
            if parsed_key is None:
                continue

            self._regimes[parsed_key] = RegimeState(
                key=parsed_key,
                minimum_setpoint=minimum_setpoint,
                completed_cycles=max(0, completed),
                stable_cycles=max(0, stable),
                last_seen=self._parse_last_seen(item.get(STORAGE_KEY_LAST_SEEN)) or now,
            )

        try:
            last_value = data.get(STORAGE_KEY_VALUE)
            self._value = float(last_value) if last_value is not None else None
        except (TypeError, ValueError):
            self._value = None

        self._prune_regimes(now)

        _LOGGER.debug("Loaded minimum setpoint state from storage: %d regimes.", len(self._regimes))

    async def async_save_regimes(self, _time: Optional[datetime] = None) -> None:
        if self._store is None:
            return

        regimes_data: Dict[str, Dict[str, Any]] = {}
        for key, state in self._regimes.items():
            regimes_data[key.to_storage()] = {
                STORAGE_KEY_MINIMUM_SETPOINT: state.minimum_setpoint,
                STORAGE_KEY_COMPLETED_CYCLES: state.completed_cycles,
                STORAGE_KEY_STABLE_CYCLES: state.stable_cycles,
                STORAGE_KEY_LAST_SEEN: state.last_seen.isoformat() if state.last_seen is not None else None,
            }

        data: Dict[str, Any] = {
            STORAGE_KEY_VALUE: self._value,
            STORAGE_KEY_REGIMES: regimes_data,
        }

        await self._store.async_save(data)
        _LOGGER.debug("Saved minimum setpoint state to storage (%d regimes).", len(self._regimes))

    def _prune_regimes(self, time: datetime) -> None:
        cutoff = time - timedelta(days=REGIME_RETENTION_DAYS)
        stale_keys = [key for key, state in self._regimes.items() if state.last_seen is not None and state.last_seen < cutoff]

        if not stale_keys:
            return

        for key in stale_keys:
            self._regimes.pop(key, None)

        if self._active_regime is not None and self._active_regime.key in stale_keys:
            self._active_regime = None

    def _seed_for_regime(self, active_key: RegimeKey, boiler_control_intent: BoilerControlIntent, boiler_capabilities: BoilerCapabilities) -> float:
        if (initial_minimum := RegimeSeeder.initial_minimum(active_key, self._regimes, self._value)) is not None:
            return clamp(initial_minimum, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

        if self._value is not None:
            return clamp(self._value, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

        return clamp(
            boiler_control_intent.setpoint,
            boiler_capabilities.minimum_setpoint,
            boiler_capabilities.maximum_setpoint,
        )

    def _make_regime_key(self, sample: ControlLoopSample) -> RegimeKey:
        """Build a stable key using hysteresis buckets to avoid flapping regimes."""
        setpoint = self._select_regime_setpoint(sample)
        if setpoint is None:
            raise ValueError("Cannot build regime key without a setpoint.")

        key = self._bucketizer.make_key(RegimeSample(
            setpoint=setpoint,
            delta_value=sample.state.flow_return_delta,
            outside_temperature=sample.outside_temperature,
        ))

        return key

    def _select_regime_setpoint(self, sample: ControlLoopSample) -> Optional[float]:
        if sample.requested_setpoint is not None:
            self._setpoint_history.append(sample.requested_setpoint)
            return sum(self._setpoint_history) / len(self._setpoint_history)

        return sample.intent.setpoint

    @staticmethod
    def _parse_last_seen(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None

        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


__all__ = [
    "RegimeKey",
    "RegimeState",
    "RegimeSeeder",
    "MinimumSetpointConfig",
    "DynamicMinimumSetpoint",
]
