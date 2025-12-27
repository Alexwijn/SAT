from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .boiler import BoilerState
from .const import CycleClassification
from .cycles import CycleKind
from .helpers import clamp

if TYPE_CHECKING:
    from .area import AreasSnapshot
    from .cycles import CycleStatistics, Cycle

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
FLOOR_MARGIN = 3


@dataclass(frozen=True, slots=True)
class MinimumSetpointConfig:
    # The absolute allowed range for any setpoint
    minimum_setpoint: float
    maximum_setpoint: float

    # How quickly the learned minimum moves when we detect a clear error
    increase_step: float = 1.0  # when minimum is too low (underheat / too short)
    decrease_step: float = 1.0  # when minimum is too high (overshoot / short-cycling)

    # Low-load detection thresholds (when we care about minimum tuning)
    low_load_minimum_cycles_per_hour: float = 3.0
    low_load_maximum_duty_ratio_15m: float = 0.50

    # Minimum samples in history before trusting the low-load regime
    minimum_on_samples_for_tuning: int = 3

    # Minimum fraction of cycle that must be space heating to consider it
    min_space_heating_fraction_for_tuning: float = 0.6

    # When learning, only trust cycles whose average setpoint is close to the current learned minimum.
    minimum_setpoint_learning_band: float = 2.0

    # Offset decay factors in various cases
    minimum_relax_factor_when_untunable: float = 0.9
    minimum_relax_factor_when_uncertain: float = 0.95

    # On large requested_setpoint jumps, reduce the impact of the previously learned minimum so we do not starve completely different regimes.
    large_jump_damping_factor: float = 0.5
    max_setpoint_jump_without_damping: float = 10.0

    # Safety: maximum deviation from the typical requested_setpoint we allow for the learned minimum.
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
        self._value: Optional[float] = None
        self._hass: Optional[HomeAssistant] = None

        self._regimes: Dict[str, RegimeState] = {}
        self._active_regime_key: Optional[str] = None
        self._previous_active_area_bucket: Optional[str] = None
        self._previous_demand_weight_bucket: Optional[str] = None

    @property
    def value(self) -> float:
        if self._value is None:
            return self._config.minimum_setpoint

        return self._value

    def reset(self) -> None:
        """Reset learned minimums and internal state."""
        self._active_regime_key = None
        self._regimes.clear()
        self._value = None

    def on_cycle_start(
            self,
            cycles: CycleStatistics,
            requested_setpoint: Optional[float],
            outside_temperature: Optional[float],
            areas_snapshot: Optional[AreasSnapshot] = None,
    ) -> None:
        if requested_setpoint is None:
            return

        self._active_regime_key = self._make_regime_key(
            cycles=cycles,
            requested_setpoint=requested_setpoint,
            outside_temperature=outside_temperature,
            areas_snapshot=areas_snapshot,
        )

    def on_cycle_end(
            self,
            boiler_state: BoilerState,
            cycles: "CycleStatistics",
            last_cycle: "Cycle",
            requested_setpoint: Optional[float]
    ) -> None:
        if requested_setpoint is None or self._active_regime_key is None:
            return

        regime_state = self._regimes.get(
            self._active_regime_key
        )

        if regime_state is None:
            initial_minimum = self._initial_minimum_for_regime(
                self._active_regime_key
            )

            if initial_minimum is None:
                initial_minimum = last_cycle.max_flow_temperature

            regime_state = RegimeState(minimum_setpoint=initial_minimum)
            self._regimes[self._active_regime_key] = regime_state

            _LOGGER.debug("Initialized regime %s with minimum_setpoint=%.1f from requested_setpoint=%.1f", self._active_regime_key, initial_minimum, requested_setpoint)

        # Mark a cycle as completed.
        regime_state.completed_cycles += 1

        # Update the count of cycles and possibly adjust the learned minimum when a cycle has just completed.
        self._maybe_tune_minimum(regime_state, boiler_state, cycles, last_cycle, requested_setpoint)

        if self._hass is not None:
            self._hass.create_task(self.async_save_regimes())
        else:
            _LOGGER.debug("Cannot save minimum setpoint regimes: hass not set")

        self._value = regime_state.minimum_setpoint

    async def async_added_to_hass(self, hass: HomeAssistant, device_id: str) -> None:
        self._hass = hass

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
                completed_cycles=max(0, completed),
                minimum_setpoint=self._clamp_setpoint(minimum),
            )

        try:
            last_value = data.get("value")
            self._value = float(last_value) if last_value is not None else None
        except (TypeError, ValueError):
            self._value = None

        _LOGGER.debug("Loaded minimum setpoint state from storage: %d regimes.", len(self._regimes))

    async def async_save_regimes(self, _time: Optional[datetime] = None) -> None:
        if self._store is None:
            return

        regimes_data: Dict[str, Dict[str, Any]] = {}
        for key, state in self._regimes.items():
            regimes_data[str(key)] = {
                "minimum_setpoint": state.minimum_setpoint,
                "completed_cycles": state.completed_cycles,
            }

        data: Dict[str, Any] = {
            "value": self._value,
            "regimes": regimes_data,
            "version": STORAGE_VERSION,
        }

        await self._store.async_save(data)
        _LOGGER.debug("Saved minimum setpoint state to storage (%d regimes).", len(self._regimes))

    def _initial_minimum_for_regime(self, new_regime_key: str) -> Optional[float]:
        if not self._regimes:
            return None

        temperature_band_order: dict[str, int] = {
            "unknown": 0,
            "freezing": 1,
            "cold": 2,
            "mild": 3,
            "warm": 4,
        }

        def regime_distance(key: str) -> tuple[int, int, int]:
            parts_a = key.split(":")
            parts_b = new_regime_key.split(":")

            setpoint_a = int(parts_a[0])
            setpoint_b = int(parts_b[0])

            temperature_a = temperature_band_order.get(parts_a[1], 0) if len(parts_a) > 1 else 0
            temperature_b = temperature_band_order.get(parts_b[1], 0) if len(parts_b) > 1 else 0

            primary = abs(setpoint_a - setpoint_b)
            secondary = abs(temperature_a - temperature_b)

            trv_mismatch = 0
            if len(parts_a) > 3 and len(parts_b) > 3 and parts_a[3] != parts_b[3]:
                trv_mismatch += 1
            if len(parts_a) > 4 and len(parts_b) > 4 and parts_a[4] != parts_b[4]:
                trv_mismatch += 1

            return primary, secondary, trv_mismatch

        closest_key = min(self._regimes.keys(), key=regime_distance)
        closest_state = self._regimes.get(closest_key)

        if closest_state is None:
            return None

        return self._clamp_setpoint(closest_state.minimum_setpoint)

    def _maybe_tune_minimum(self, regime_state: "RegimeState", boiler_state_at_end: "BoilerState", statistics: "CycleStatistics", last_cycle: Cycle, requested_setpoint: float) -> None:
        """Decide whether and how to adjust the learned minimum setpoint for the active regime after a cycle. """
        if self._active_regime_key is None:
            return

        # Check if the current regime is suitable for minimum tuning.
        if not self._is_tunable_regime(boiler_state_at_end, statistics):
            self._relax_toward_anchor(regime_state, last_cycle, requested_setpoint, self._config.minimum_relax_factor_when_untunable)
            return

        # Only use cycles that are predominantly space heating.
        if last_cycle.kind not in (CycleKind.CENTRAL_HEATING, CycleKind.MIXED):
            _LOGGER.debug("Ignoring non-heating cycle kind=%s for tuning.", last_cycle.kind)
            return

        if last_cycle.fraction_space_heating < self._config.min_space_heating_fraction_for_tuning:
            _LOGGER.debug("Cycle has too little space-heating fraction (%.2f), ignoring.", last_cycle.fraction_space_heating)
            return

        classification = last_cycle.classification
        average_setpoint = last_cycle.average_setpoint

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
        #   - This means the requested flow temperature is *too high*.
        # LONG_UNDERHEAT:
        #   - The cycle ended too long (long flame ON time).
        #   - The boiler did not approach the requested flow setpoint.
        #   - This means the requested flow temperature is *too high*.
        if classification in (CycleClassification.TOO_SHORT_UNDERHEAT, CycleClassification.LONG_UNDERHEAT):
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
            self._relax_toward_anchor(regime_state, last_cycle, requested_setpoint, self._config.minimum_relax_factor_when_uncertain)
            return

        # Clamp learned the minimum for this regime to absolute range.
        regime_state.minimum_setpoint = self._clamp_setpoint(regime_state.minimum_setpoint)
        _LOGGER.debug("Updated regime %s minimum_setpoint=%.1f after cycle.", self._active_regime_key, regime_state.minimum_setpoint, )

    def _is_tunable_regime(self, boiler_state: BoilerState, statistics: CycleStatistics) -> bool:
        """Decide whether the current conditions are suitable for minimum tuning."""
        if boiler_state.hot_water_active:
            return False

        if boiler_state.is_inactive:
            return False

        if statistics.sample_count_4h < self._config.minimum_on_samples_for_tuning:
            return False

        if statistics.last_hour_count < self._config.low_load_minimum_cycles_per_hour:
            return False

        return True

    def _relax_toward_anchor(self, regime_state: "RegimeState", last_cycle: "Cycle", requested_setpoint: float, factor: float) -> None:
        """ Relax the regime minimum toward the closest anchor. """
        if factor <= 0.0 or factor >= 1.0:
            # If the factor is out of range, do nothing.
            return

        anchor = requested_setpoint
        if last_cycle.max_flow_temperature is not None:
            effective_floor = last_cycle.max_flow_temperature - FLOOR_MARGIN
            anchor = max(anchor, effective_floor)

        old = regime_state.minimum_setpoint
        new = factor * old + (1.0 - factor) * anchor

        _LOGGER.debug(
            "Relaxing regime %s minimum toward anchor=%.1f: %.1f -> %.1f (factor=%.2f)",
            self._active_regime_key, anchor, old, new, factor
        )

        regime_state.minimum_setpoint = self._clamp_setpoint(new)

    def _make_regime_key(
            self,
            cycles: "CycleStatistics",
            requested_setpoint: float,
            outside_temperature: Optional[float],
            areas_snapshot: Optional["AreasSnapshot"],
    ) -> str:
        setpoint_band = int(requested_setpoint // self._config.regime_band_width)

        if outside_temperature is None:
            temperature_band = "unknown"
        elif outside_temperature < 0.0:
            temperature_band = "freezing"
        elif outside_temperature < 5.0:
            temperature_band = "cold"
        elif outside_temperature < 15.0:
            temperature_band = "mild"
        else:
            temperature_band = "warm"

        # Load band (keep coarse and stable)
        if cycles.sample_count_4h < max(6, self._config.minimum_on_samples_for_tuning):
            load_band = "unknown"
        else:
            is_low_load = (
                    cycles.last_hour_count >= self._config.low_load_minimum_cycles_per_hour
                    and cycles.duty_ratio_last_15m <= self._config.low_load_maximum_duty_ratio_15m
            )
            load_band = "low" if is_low_load else "normal"

        # TRV-derived regime dimensions (coarse + hysteresis)
        active_area_bucket = "sec_unknown"
        demand_weight_bucket = "w_unknown"

        if areas_snapshot is not None:
            active_area_bucket = self._bucket_active_area_count_with_hysteresis(areas_snapshot.active_area_count)
            demand_weight_bucket = self._bucket_demand_weight_with_hysteresis(areas_snapshot.demand_weight_sum or 0.0)

        return f"{setpoint_band}:{temperature_band}:{load_band}:{active_area_bucket}:{demand_weight_bucket}"

    def _bucket_active_area_count_with_hysteresis(self, active_area_count: int) -> str:
        previous_bucket = self._previous_demand_weight_bucket

        if previous_bucket is None:
            if active_area_count <= 0:
                bucket = "sec0"
            elif active_area_count == 1:
                bucket = "sec1"
            elif active_area_count <= 3:
                bucket = "sec2-3"
            else:
                bucket = "sec4+"

            self._previous_active_area_bucket = bucket
            return bucket

        # Hysteresis rules: require a full step change to move buckets
        if previous_bucket == "sec0":
            if active_area_count >= 2:
                previous_bucket = "sec2-3"
            elif active_area_count == 1:
                previous_bucket = "sec1"

        elif previous_bucket == "sec1":
            if active_area_count <= 0:
                previous_bucket = "sec0"
            elif active_area_count >= 3:
                previous_bucket = "sec2-3"

        elif previous_bucket == "sec2-3":
            if active_area_count <= 1:
                previous_bucket = "sec1"
            elif active_area_count >= 5:
                previous_bucket = "sec4+"

        elif previous_bucket == "sec4+":
            if active_area_count <= 3:
                previous_bucket = "sec2-3"

        self._previous_active_area_bucket = previous_bucket
        return previous_bucket

    def _bucket_demand_weight_with_hysteresis(self, demand_weight_sum: float) -> str:

        previous_bucket = self._previous_demand_weight_bucket

        # Thresholds (coarse).
        low_threshold = 0.6
        medium_threshold = 1.5
        high_threshold = 3.0

        # Hysteresis margins
        margin = 0.15

        if previous_bucket is None:
            if demand_weight_sum < low_threshold:
                bucket = "w_none"
            elif demand_weight_sum < medium_threshold:
                bucket = "w_low"
            elif demand_weight_sum < high_threshold:
                bucket = "w_med"
            else:
                bucket = "w_high"

            self._previous_demand_weight_bucket = bucket
            return bucket

        # Stickiness around thresholds
        if previous_bucket == "w_none":
            if demand_weight_sum >= low_threshold + margin:
                previous_bucket = "w_low"

        elif previous_bucket == "w_low":
            if demand_weight_sum < low_threshold - margin:
                previous_bucket = "w_none"
            elif demand_weight_sum >= medium_threshold + margin:
                previous_bucket = "w_med"

        elif previous_bucket == "w_med":
            if demand_weight_sum < medium_threshold - margin:
                previous_bucket = "w_low"
            elif demand_weight_sum >= high_threshold + margin:
                previous_bucket = "w_high"

        elif previous_bucket == "w_high":
            if demand_weight_sum < high_threshold - margin:
                previous_bucket = "w_med"

        self._previous_demand_weight_bucket = previous_bucket
        return previous_bucket

    def _clamp_setpoint(self, value: float) -> float:
        return clamp(value, self._config.minimum_setpoint, self._config.maximum_setpoint)
