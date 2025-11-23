from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .boiler import BoilerState
from .const import CycleClassification
from .cycles import CycleKind, CycleStatistics, Cycle
from .helpers import clamp

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


@dataclass(frozen=True, slots=True)
class MinimumSetpointConfig:
    # The absolute allowed range for any setpoint
    minimum_setpoint: float
    maximum_setpoint: float

    # How quickly the learned minimum moves when we detect a clear error
    increase_step: float = 1.0  # when minimum is too low (underheat / too short)
    decrease_step: float = 1.0  # when minimum is too high (overshoot / short-cycling)

    # Target minimum ON duration for a "good" burn in low-load conditions
    target_min_on_time_seconds: float = 300.0  # 5 minutes

    # Low-load detection thresholds (when we care about minimum tuning)
    low_load_minimum_cycles_per_hour: float = 3.0
    low_load_maximum_duty_ratio_15m: float = 0.50

    # Minimum samples in history before trusting the low-load regime
    minimum_on_samples_for_tuning: int = 3

    # Minimum fraction of cycle that must be space heating to consider it
    min_space_heating_fraction_for_tuning: float = 0.6

    # When learning, only trust cycles whose average setpoint is close to the current learned minimum.
    # This helps keep the learned minimum stable during the day even if base_setpoint moves a lot.
    minimum_setpoint_learning_band: float = 2.0

    # Offset decay factors in various cases
    minimum_relax_factor_when_inactive: float = 0.5
    minimum_relax_factor_when_untunable: float = 0.9
    minimum_relax_factor_when_uncertain: float = 0.95

    # On large base_setpoint jumps, reduce the impact of the previously learned minimum so we do not starve completely different regimes.
    large_jump_damping_factor: float = 0.5
    max_setpoint_jump_without_damping: float = 10.0

    # Safety: maximum deviation from the typical base_setpoint we allow for the learned minimum.
    max_deviation_from_recent_base: float = 15.0

    # How many completed cycles we require after the first initialization before we start tuning.
    warmup_cycles_before_tuning: int = 1

    # Regime grouping: bucket base setpoint into bands so we can remember different regimes.
    regime_band_width: float = 3.0


@dataclass(slots=True)
class RegimeState:
    minimum_setpoint: float
    completed_cycles: int = 0


class DynamicMinimumSetpoint:

    def __init__(self, config: MinimumSetpointConfig) -> None:

        self._config = config
        self._store: Optional[Store] = None

        # Learned per-regime minimum setpoints.
        self._regimes: Dict[str, RegimeState] = {}

        # Currently active regime key.
        self._active_regime_key: Optional[str] = None

        # Last seen requested_setpoint, for jump detection and safety.
        self._last_base_setpoint: Optional[float] = None

    @property
    def value(self) -> float:
        """Return the learned minimum setpoint for the current regime."""
        if self._active_regime_key is not None and self._active_regime_key in self._regimes:
            minimum_setpoint = self._regimes[self._active_regime_key].minimum_setpoint
        elif self._regimes:
            minimum_setpoint = min(regime.minimum_setpoint for regime in self._regimes.values())
        else:
            return self._config.minimum_setpoint

        # Additional guard: do not allow minimum to drift too far below recent bases.
        if self._last_base_setpoint is not None:
            allowed_minimum = max(self._config.minimum_setpoint, self._last_base_setpoint - self._config.max_deviation_from_recent_base)
            if minimum_setpoint < allowed_minimum:
                minimum_setpoint = allowed_minimum

        return self._clamp_setpoint(minimum_setpoint)

    def reset(self) -> None:
        """Reset learned minimums and internal state."""
        self._last_base_setpoint = None
        self._active_regime_key = None
        self._regimes.clear()

    def update(self, boiler_state: BoilerState, cycles: CycleStatistics, last_cycle: Cycle, requested_setpoint: Optional[float]) -> None:
        """Update the controller with the latest state."""
        if requested_setpoint is None:
            return

        # Determine the active regime for this requested_setpoint.
        regime_key = self._make_regime_key(requested_setpoint)
        self._active_regime_key = regime_key

        # Ensure regime exists.
        regime_state = self._regimes.get(regime_key)
        if regime_state is None:
            initial_minimum = self._initial_minimum_for_regime(regime_key, requested_setpoint)
            regime_state = RegimeState(minimum_setpoint=initial_minimum, completed_cycles=0)

            self._regimes[regime_key] = regime_state
            _LOGGER.debug("Initialized regime %s with minimum_setpoint=%.1f from requested_setpoint=%.1f", regime_key, initial_minimum, requested_setpoint)

        # Handle large jumps in requested_setpoint (regime changes).
        self._maybe_damp_on_large_jump(requested_setpoint, regime_state)

        # Update the count of cycles and possibly adjust the learned minimum when a cycle has just completed.
        if last_cycle is not None:
            regime_state.completed_cycles += 1
            self._maybe_tune_minimum(regime_state, boiler_state, cycles, last_cycle, base_setpoint=requested_setpoint)

        self._last_base_setpoint = requested_setpoint

    async def async_added_to_hass(self, hass: HomeAssistant, device_id: str) -> None:
        if self._store is None:
            self._store = Store(hass, STORAGE_VERSION, f"sat.minimum_setpoint.{device_id}")

        data: Optional[Dict[str, Any]] = await self._store.async_load()
        if not data:
            return

        version = data.get("version")
        if version != STORAGE_VERSION:
            _LOGGER.debug("Unknown minimum setpoint storage version: %s", version)

        regimes_data = data.get("regimes", {})
        self._regimes.clear()

        for key, item in regimes_data.items():
            if not isinstance(item, dict):
                continue

            try:
                minimum = float(item["minimum_setpoint"])
            except (KeyError, TypeError, ValueError):
                continue

            try:
                completed = int(item.get("completed_cycles", 0))
            except (TypeError, ValueError):
                completed = 0

            self._regimes[str(key)] = RegimeState(
                minimum_setpoint=self._clamp_setpoint(minimum),
                completed_cycles=max(0, completed),
            )

        last_base = data.get("last_base_setpoint")

        try:
            self._last_base_setpoint = float(last_base) if last_base is not None else None
        except (TypeError, ValueError):
            self._last_base_setpoint = None

        _LOGGER.debug("Loaded minimum setpoint state from storage: %d regimes, last_base_setpoint=%s", len(self._regimes), self._last_base_setpoint)

    async def async_will_remove_from_hass(self) -> None:
        if self._store is None:
            return

        regimes_data: Dict[str, Dict[str, Any]] = {}
        for key, state in self._regimes.items():
            regimes_data[str(key)] = {
                "minimum_setpoint": state.minimum_setpoint,
                "completed_cycles": state.completed_cycles,
            }

        data: Dict[str, Any] = {
            "version": STORAGE_VERSION,
            "regimes": regimes_data,
            "last_base_setpoint": self._last_base_setpoint,
        }

        await self._store.async_save(data)
        _LOGGER.debug("Saved minimum setpoint state to storage (%d regimes).", len(self._regimes))

    def _make_regime_key(self, base_setpoint: float) -> str:
        """
        Create a coarse regime key from the requested setpoint.

        This buckets base_setpoint into bands of width regime_band_width.
        """
        width = self._config.regime_band_width
        if width <= 0.0:
            width = 1.0

        bucket = int(round(base_setpoint / width))
        return f"b:{bucket}"

    def _initial_minimum_for_regime(self, regime_key: str, base_setpoint: float) -> float:
        # If we already have regimes, reuse the nearest one (unchanged)
        if self._regimes:
            try:
                target_bucket = int(regime_key.split(":", 1)[1])
            except (IndexError, ValueError):
                target_bucket = 0

            def bucket_of(key: str) -> int:
                try:
                    return int(key.split(":", 1)[1])
                except (IndexError, ValueError):
                    return 0

            nearest_key = min(self._regimes.keys(), key=lambda key: abs(bucket_of(key) - target_bucket))
            return self._clamp_setpoint(self._regimes[nearest_key].minimum_setpoint)

        return self._clamp_setpoint(base_setpoint)

    def _maybe_tune_minimum(self, regime_state: RegimeState, boiler_state_at_end: BoilerState, statistics: CycleStatistics, cycle: Cycle, base_setpoint: float) -> None:
        """ Decide whether and how to adjust the learned minimum setpoint for the active regime after a cycle. """
        if self._active_regime_key is None or cycle is None:
            return

        # Do not tune during the initial warmup cycles after starting or reset for this regime.
        if regime_state.completed_cycles <= self._config.warmup_cycles_before_tuning:
            _LOGGER.debug(
                "Regime %s: in warmup period (%d <= %d), not tuning yet.",
                self._active_regime_key, regime_state.completed_cycles, self._config.warmup_cycles_before_tuning,
            )
            return

        # Check if the current regime is suitable for minimum tuning.
        if not self._is_tunable_regime(boiler_state_at_end, statistics):
            # Gently relax toward base_setpoint in non-tunable regimes.
            self._relax_toward_base(regime_state, base_setpoint, self._config.minimum_relax_factor_when_untunable)
            return

        # Only use cycles that are predominantly space heating.
        if cycle.kind not in (CycleKind.CENTRAL_HEATING, CycleKind.MIXED):
            _LOGGER.debug("Ignoring non-heating cycle kind=%s for tuning.", cycle.kind)
            return

        if cycle.fraction_space_heating < self._config.min_space_heating_fraction_for_tuning:
            _LOGGER.debug("Cycle has too little space-heating fraction (%.2f), ignoring.", cycle.fraction_space_heating)
            return

        classification = cycle.classification
        average_setpoint = cycle.average_setpoint

        if average_setpoint is None:
            average_setpoint = boiler_state_at_end.setpoint

        if average_setpoint is None:
            _LOGGER.debug("No average setpoint for cycle, skipping tuning.")
            return

        current_minimum = regime_state.minimum_setpoint

        if abs(average_setpoint - current_minimum) > self._config.minimum_setpoint_learning_band:
            _LOGGER.debug(
                "Cycle average_setpoint=%.1f is too far from regime minimum_setpoint=%.1f (band=%.1f), skipping tuning.",
                average_setpoint, current_minimum, self._config.minimum_setpoint_learning_band,
            )
            return

        _LOGGER.debug(
            "Cycle classification=%s, duration=%.1fs, cycles_last_hour=%.1f, duty_15m=%.2f, avg_setpoint=%.1f, min_flow=%.1f, max_flow=%.1f",
            classification, cycle.duration, statistics.cycles_last_hour, statistics.duty_ratio_last_15m, average_setpoint,
            cycle.min_flow_temperature if cycle.min_flow_temperature is not None else float("nan"),
            cycle.max_flow_temperature if cycle.max_flow_temperature is not None else float("nan"),
        )

        # GOOD:
        #   The boiler produced a long, stable burn without overshoot or underheat.
        #   This means the current minimum_setpoint is appropriate for this regime.
        #
        # INSUFFICIENT_DATA:
        #   We do not know enough to make a safe decision.
        if classification in (CycleClassification.GOOD, CycleClassification.INSUFFICIENT_DATA):
            return

        # TOO_SHORT_UNDERHEAT:
        #   - The cycle ended too quickly (short flame ON time).
        #   - The boiler NEVER approached the requested flow setpoint.
        #   - This means the *requested flow temperature is too high* for the current
        #     thermal regime or boiler output at minimum modulation.
        if classification == CycleClassification.TOO_SHORT_UNDERHEAT:
            # If modulation is extremely low (< 20%), we cannot trust the underheated signal.
            if cycle.average_relative_modulation_level is not None and cycle.average_relative_modulation_level < 20:
                self._relax_toward_base(regime_state, base_setpoint, self._config.minimum_relax_factor_when_uncertain)
                return

            # The requested setpoint is too high for this regime. Lower the learned minimum_setpoint.
            regime_state.minimum_setpoint -= self._config.decrease_step

        # TOO_SHORT_OVERSHOOT:
        #   - Short burn AND flow shoots past setpoint.
        #   - The requested flow temperature is too *low* for stable operation.
        #
        # SHORT_CYCLING_OVERSHOOT:
        #   - Long-ish burns but high cycles/hour and overshoot.
        #   - Also indicates the requested setpoint is too low for stable operation.
        elif classification in (CycleClassification.TOO_SHORT_OVERSHOOT, CycleClassification.SHORT_CYCLING_OVERSHOOT):
            regime_state.minimum_setpoint += self._config.increase_step

        # UNCERTAIN:
        #   - Conflicting signals, borderline flows, or sensor noise.
        #   - Neither direction (up or down) is reliable.
        elif classification == CycleClassification.UNCERTAIN:
            self._relax_toward_base(regime_state, base_setpoint, self._config.minimum_relax_factor_when_uncertain)
            return

        # Clamp learned the minimum for this regime to absolute range.
        regime_state.minimum_setpoint = self._clamp_setpoint(regime_state.minimum_setpoint)
        _LOGGER.debug("Updated regime %s minimum_setpoint=%.1f after cycle.", self._active_regime_key, regime_state.minimum_setpoint, )

    def _is_tunable_regime(self, boiler_state: BoilerState, statistics: CycleStatistics) -> bool:
        """Decide whether the current conditions are suitable for minimum tuning."""
        if boiler_state.hot_water_active:
            return False

        if not boiler_state.is_active:
            return False

        if statistics.sample_count_4h < self._config.minimum_on_samples_for_tuning:
            return False

        if statistics.cycles_last_hour < self._config.low_load_minimum_cycles_per_hour:
            return False

        if statistics.duty_ratio_last_15m > self._config.low_load_maximum_duty_ratio_15m:
            return False

        return True

    def _relax_minimum_when_uncertain(self) -> None:
        """Relax minimum slightly for the active regime when classification is uncertain."""
        if self._active_regime_key is None:
            return

        regime_state = self._regimes.get(self._active_regime_key)
        if regime_state is None:
            return

        factor = self._config.minimum_relax_factor_when_uncertain
        relaxed = self._config.minimum_setpoint + (regime_state.minimum_setpoint - self._config.minimum_setpoint) * factor

        _LOGGER.debug("Relaxing minimum (uncertain) for regime %s: %.1f -> %.1f", self._active_regime_key, regime_state.minimum_setpoint, relaxed)
        regime_state.minimum_setpoint = self._clamp_setpoint(relaxed)

    def _relax_toward_base(self, regime_state: RegimeState, base_setpoint: float, factor: float) -> None:
        """
        Relax the regime minimum toward the base_setpoint.
        """
        if factor <= 0.0 or factor >= 1.0:
            # If the factor is out of range, do nothing.
            return

        current = regime_state.minimum_setpoint
        relaxed = base_setpoint + (current - base_setpoint) * factor
        relaxed = self._clamp_setpoint(relaxed)

        _LOGGER.debug(
            "Relaxing regime %s minimum toward base_setpoint=%.1f: %.1f -> %.1f (factor=%.2f)",
            self._active_regime_key, base_setpoint, current, relaxed, factor
        )

        regime_state.minimum_setpoint = relaxed

    def _maybe_damp_on_large_jump(self, base_setpoint: float, regime_state: RegimeState) -> None:
        """
        When base_setpoint jumps a lot (for example, cold morning start),
        damp the learned minimum for the active regime so it does not apply too aggressively in a new regime.
        """

        if self._last_base_setpoint is None:
            return

        jump = abs(base_setpoint - self._last_base_setpoint)
        if jump <= self._config.max_setpoint_jump_without_damping:
            return

        old_minimum = regime_state.minimum_setpoint
        regime_state.minimum_setpoint = self._config.minimum_setpoint + (regime_state.minimum_setpoint - self._config.minimum_setpoint) * self._config.large_jump_damping_factor
        regime_state.minimum_setpoint = self._clamp_setpoint(regime_state.minimum_setpoint)

        _LOGGER.debug(
            "Large base_setpoint jump (%.1f -> %.1f, delta=%.1f).  Damping learned minimum for regime %s: %.1f -> %.1f",
            self._last_base_setpoint, base_setpoint, jump, self._active_regime_key, old_minimum, regime_state.minimum_setpoint
        )

    def _clamp_setpoint(self, value: float) -> float:
        return clamp(value, self._config.minimum_setpoint, self._config.maximum_setpoint)
