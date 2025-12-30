from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .boiler import BoilerState
from .const import CycleClassification
from .cycles import CycleKind, TARGET_MIN_ON_TIME_SECONDS, ULTRA_SHORT_MIN_ON_TIME_SECONDS
from .helpers import clamp

if TYPE_CHECKING:
    from .cycles import CycleStatistics, Cycle

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


@dataclass(frozen=True, slots=True)
class MinimumSetpointConfig:
    # The absolute allowed range for any setpoint
    minimum_setpoint: float
    maximum_setpoint: float

    # Low-load detection thresholds (when we care about minimum tuning)
    low_load_minimum_cycles_per_hour: float = 3.0
    low_load_maximum_duty_ratio_15m: float = 0.50

    # Minimum samples in history before trusting the low-load regime
    minimum_on_samples_for_tuning: int = 3

    # Minimum fraction of cycle that must be space heating to consider it
    min_space_heating_fraction_for_tuning: float = 0.6

    # When learning, only trust cycles whose setpoint is close to the current learned minimum.
    minimum_setpoint_learning_band: float = 4.0

    # Offset decay factors in various cases
    minimum_relax_factor_when_untunable: float = 0.9
    minimum_relax_factor_when_uncertain: float = 0.95

    # Regime grouping: bucket base setpoint into bands so we can remember different regimes.
    regime_band_width: float = 3.0

    floor_margin: float = 3.0
    min_stable_cycles_to_trust: float = 2


@dataclass(slots=True)
class RegimeState:
    minimum_setpoint: float

    stable_cycles: int = 0
    completed_cycles: int = 0


class DynamicMinimumSetpoint:
    def __init__(self, config: MinimumSetpointConfig) -> None:

        self._config = config
        self._store: Optional[Store] = None
        self._value: Optional[float] = None
        self._hass: Optional[HomeAssistant] = None

        self._regimes: Dict[str, RegimeState] = {}
        self._active_regime_key: Optional[str] = None

        self._previous_delta_bucket: Optional[str] = None
        self._previous_setpoint_band: Optional[int] = None
        self._previous_outside_temperature_bucket: Optional[str] = None

    @property
    def value(self) -> float:
        if self._value is None:
            return self._config.minimum_setpoint

        return clamp(self._value, self._config.minimum_setpoint, self._config.maximum_setpoint)

    def reset(self) -> None:
        """Reset learned minimums and internal state."""
        self._value = None
        self._regimes.clear()
        self._active_regime_key = None

        self._previous_delta_bucket = None
        self._previous_setpoint_band = None
        self._previous_outside_temperature_bucket = None

    def on_cycle_start(self, cycles: CycleStatistics, requested_setpoint: Optional[float], outside_temperature: Optional[float]) -> None:
        if requested_setpoint is None:
            return

        self._active_regime_key = self._make_regime_key(
            cycles=cycles,
            requested_setpoint=requested_setpoint,
            outside_temperature=outside_temperature,
        )

        regime_state = self._regimes.get(self._active_regime_key)
        if regime_state is None:
            regime_state = RegimeState(minimum_setpoint=self._seed_minimum_for_new_regime(
                new_regime_key=self._active_regime_key,
                requested_setpoint=requested_setpoint
            ))

            self._regimes[self._active_regime_key] = regime_state
            _LOGGER.info("Initialized regime %s at cycle start with minimum_setpoint=%.2f", self._active_regime_key, regime_state.minimum_setpoint)

        self._value = regime_state.minimum_setpoint

    def on_cycle_end(self, boiler_state: BoilerState, cycles: "CycleStatistics", last_cycle: "Cycle", requested_setpoint: Optional[float]) -> None:
        if requested_setpoint is None or self._active_regime_key is None:
            return

        regime_state = self._regimes.get(self._active_regime_key)
        _LOGGER.debug("Cycle ended: regime=%s classification=%s requested_setpoint=%.2f", self._active_regime_key, last_cycle.classification.name, requested_setpoint)

        # Mark a cycle as completed.
        regime_state.completed_cycles += 1
        _LOGGER.debug("Regime %s completed_cycles=%d", self._active_regime_key, regime_state.completed_cycles)

        # Mark a cycle as stable when the classification is GOOD.
        if last_cycle.classification == CycleClassification.GOOD:
            regime_state.stable_cycles += 1
            _LOGGER.debug("Regime %s stable cycle detected (stable_cycles=%d)", self._active_regime_key, regime_state.stable_cycles)

        # Track before/after for tuning visibility
        previous_minimum = regime_state.minimum_setpoint
        self._maybe_tune_minimum(regime_state, boiler_state, cycles, last_cycle, requested_setpoint)

        # Clamp learned minimum for this regime to absolute range.
        regime_state.minimum_setpoint = clamp(regime_state.minimum_setpoint, self._config.minimum_setpoint, self._config.maximum_setpoint)

        if regime_state.minimum_setpoint != previous_minimum:
            _LOGGER.info("Regime %s minimum setpoint adjusted: %.2f → %.2f", self._active_regime_key, previous_minimum, regime_state.minimum_setpoint)
        else:
            _LOGGER.debug("Regime %s minimum setpoint unchanged at %.2f", self._active_regime_key, regime_state.minimum_setpoint)

        self._value = regime_state.minimum_setpoint

        if self._hass is not None:
            self._hass.create_task(self.async_save_regimes())

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
                minimum_setpoint = float(item["minimum_setpoint"])
            except (KeyError, TypeError, ValueError):
                continue

            try:
                completed = int(item.get("completed_cycles", 0))
            except (TypeError, ValueError):
                completed = 0

            try:
                stable = int(item.get("stable_cycles", 0))
            except (TypeError, ValueError):
                stable = 0

            self._regimes[str(key)] = RegimeState(
                minimum_setpoint=minimum_setpoint,
                completed_cycles=max(0, completed),
                stable_cycles=max(0, stable),
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
                "stable_cycles": state.stable_cycles,
            }

        data: Dict[str, Any] = {
            "value": self._value,
            "regimes": regimes_data,
            "version": STORAGE_VERSION,
        }

        await self._store.async_save(data)
        _LOGGER.debug("Saved minimum setpoint state to storage (%d regimes).", len(self._regimes))

    def _seed_minimum_for_new_regime(self, new_regime_key: str, requested_setpoint: float) -> float:
        if (initial_minimum := self._initial_minimum_for_regime(new_regime_key)) is not None:
            return clamp(initial_minimum, self._config.minimum_setpoint, self._config.maximum_setpoint)

        if self._value is not None:
            return clamp(self._value, self._config.minimum_setpoint, self._config.maximum_setpoint)

        return clamp(requested_setpoint, self._config.minimum_setpoint, self._config.maximum_setpoint)

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

        trusted_regimes = {
            key: state
            for key, state in self._regimes.items()
            if (state.stable_cycles >= self._config.min_stable_cycles_to_trust) and (state.completed_cycles >= 3)
        }

        if not trusted_regimes:
            return None

        def regime_distance(key: str) -> tuple[int, int, int]:
            parts_a = key.split(":")
            parts_b = new_regime_key.split(":")

            try:
                setpoint_a = int(parts_a[0])
                setpoint_b = int(parts_b[0])
            except (IndexError, ValueError):
                return 10_000, 10_000, 10_000

            temperature_a = temperature_band_order.get(parts_a[1], 0) if len(parts_a) > 1 else 0
            temperature_b = temperature_band_order.get(parts_b[1], 0) if len(parts_b) > 1 else 0

            primary = abs(setpoint_a - setpoint_b)
            secondary = abs(temperature_a - temperature_b)

            trv_mismatch = 0
            if len(parts_a) > 3 and len(parts_b) > 3 and parts_a[3] != parts_b[3]:
                trv_mismatch += 1
            if len(parts_a) > 4 and len(parts_b) > 4 and parts_a[4] != parts_b[4]:
                trv_mismatch += 1

            return primary, trv_mismatch, secondary

        closest_key = min(trusted_regimes.keys(), key=regime_distance)
        closest_state = trusted_regimes.get(closest_key)

        if closest_state is None:
            return None

        if self._value is None:
            return closest_state.minimum_setpoint
        else:
            return round(0.7 * self._value + 0.3 * closest_state.minimum_setpoint, 1)

    def _maybe_tune_minimum(self, regime_state: "RegimeState", boiler_state_at_end: "BoilerState", cycles: "CycleStatistics", last_cycle: Cycle, requested_setpoint: float) -> None:
        """Decide whether and how to adjust the learned minimum setpoint for the active regime after a cycle."""
        if self._active_regime_key is None:
            return

        # Check if the current regime is suitable for minimum tuning.
        if not self._is_tunable_regime(boiler_state_at_end, cycles):
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
        reference_setpoint = last_cycle.tail.setpoint.p50

        if reference_setpoint is None:
            reference_setpoint = boiler_state_at_end.setpoint

        if reference_setpoint is None:
            _LOGGER.debug("No setpoint found for cycle, skipping tuning.")
            return

        current_minimum = regime_state.minimum_setpoint

        if abs(reference_setpoint - current_minimum) > self._config.minimum_setpoint_learning_band:
            _LOGGER.debug(
                "Cycle reference_setpoint=%.1f is too far from regime minimum_setpoint=%.1f (band=%.1f), skipping tuning.",
                reference_setpoint, current_minimum, self._config.minimum_setpoint_learning_band,
            )
            return

        if classification in (CycleClassification.FAST_OVERSHOOT, CycleClassification.TOO_SHORT_OVERSHOOT):
            is_ultra_short = last_cycle.duration < ULTRA_SHORT_MIN_ON_TIME_SECONDS
            is_very_short = last_cycle.duration < (TARGET_MIN_ON_TIME_SECONDS * 0.5)
            is_low_duty = cycles.duty_ratio_last_15m <= self._config.low_load_maximum_duty_ratio_15m
            is_frequent_cycles = cycles.last_hour_count >= self._config.low_load_minimum_cycles_per_hour

            if is_very_short and is_low_duty and is_frequent_cycles and (not is_ultra_short):
                _LOGGER.debug(
                    "Ignoring %s for minimum tuning under low-load: duration=%.1fs (< %.1fs), duty_15m=%.2f (<= %.2f), cycles_last_hour=%.1f (>= %.1f).",
                    classification.name, last_cycle.duration, TARGET_MIN_ON_TIME_SECONDS * 0.5, cycles.duty_ratio_last_15m,
                    self._config.low_load_maximum_duty_ratio_15m, cycles.last_hour_count, self._config.low_load_minimum_cycles_per_hour,
                )
                return

        # INSUFFICIENT_DATA:
        #   We do not know enough to make a safe decision.
        if classification == CycleClassification.INSUFFICIENT_DATA:
            return

        # UNCERTAIN:
        #   - Conflicting signals, borderline flows, or sensor noise.
        #   - Neither direction (up or down) is reliable.
        elif classification == CycleClassification.UNCERTAIN:
            return

        # GOOD:
        #   The boiler produced a long, stable burn without overshoot or underheat.
        #   This means the current minimum_setpoint is appropriate for this regime.
        #   But we do try to find a better value.
        elif classification == CycleClassification.GOOD and regime_state.stable_cycles >= 3:
            regime_state.minimum_setpoint -= 0.3

        # FAST_UNDERHEAT / TOO_SHORT_UNDERHEAT:
        #   - Boiler fails to approach the requested flow temperature.
        #   - Indicates the requested flow setpoint is too high for the available heat output.
        elif classification in (CycleClassification.FAST_UNDERHEAT, CycleClassification.TOO_SHORT_UNDERHEAT):
            regime_state.minimum_setpoint -= 1.0

        # FAST_OVERSHOOT / TOO_SHORT_OVERSHOOT:
        #   - Boiler fails to stay stable at the requested flow temperature.
        #   - Indicates the requested flow setpoint is too low for the available heat output.
        elif classification in (CycleClassification.FAST_OVERSHOOT, CycleClassification.TOO_SHORT_OVERSHOOT):
            regime_state.minimum_setpoint += 1.0

        # LONG_UNDERHEAT:
        #   - Long burn, but flow temperature remains below setpoint.
        #   - Indicates chronic underheating at this setpoint.
        elif classification == CycleClassification.LONG_UNDERHEAT:
            regime_state.minimum_setpoint -= 0.5

        # LONG_OVERSHOOT:
        #   - Sustained overshoot during a longer burn.
        #   - More likely indicates the requested flow setpoint is genuinely too low for stable operation.
        elif classification == CycleClassification.LONG_OVERSHOOT:
            regime_state.minimum_setpoint += 0.5

    def _is_tunable_regime(self, boiler_state: BoilerState, cycles: CycleStatistics) -> bool:
        """Decide whether the current conditions are suitable for minimum tuning."""
        if boiler_state.is_inactive:
            return False

        if cycles.sample_count_4h < self._config.minimum_on_samples_for_tuning:
            return False

        if cycles.last_hour_count < self._config.low_load_minimum_cycles_per_hour:
            return False

        return True

    def _relax_toward_anchor(self, regime_state: "RegimeState", last_cycle: "Cycle", requested_setpoint: float, factor: float) -> None:
        """Relax the regime minimum toward a stable, outcome-derived anchor."""
        if factor <= 0.0 or factor >= 1.0:
            return

        old_minimum_setpoint = regime_state.minimum_setpoint
        tail_setpoint_p50 = last_cycle.tail.setpoint.p50
        tail_setpoint_p90 = last_cycle.tail.setpoint.p90

        cycle_setpoint_reference = tail_setpoint_p50 if tail_setpoint_p50 is not None else tail_setpoint_p90

        ran_near_minimum = False
        if cycle_setpoint_reference is not None:
            ran_near_minimum = (abs(cycle_setpoint_reference - old_minimum_setpoint) <= self._config.minimum_setpoint_learning_band)

        anchor_candidate_from_flow: Optional[float] = None
        if ran_near_minimum:
            tail_flow_p50 = last_cycle.tail.flow_temperature.p50
            tail_flow_p90 = last_cycle.tail.flow_temperature.p90
            max_flow_temperature = last_cycle.max_flow_temperature

            flow_reference = (
                tail_flow_p50
                if tail_flow_p50 is not None
                else (tail_flow_p90 if tail_flow_p90 is not None else max_flow_temperature)
            )

            if flow_reference is not None:
                anchor_candidate_from_flow = flow_reference - self._config.floor_margin

        anchor_candidate_from_tail_setpoint: Optional[float] = tail_setpoint_p50 if tail_setpoint_p50 is not None else tail_setpoint_p90
        anchor_candidate_fallback = requested_setpoint

        if anchor_candidate_from_flow is not None:
            anchor = anchor_candidate_from_flow
            anchor_source = "flow_floor"
        elif anchor_candidate_from_tail_setpoint is not None:
            anchor = anchor_candidate_from_tail_setpoint
            anchor_source = "tail_setpoint"
        else:
            anchor = anchor_candidate_fallback
            anchor_source = "requested_setpoint"

        anchor = clamp(anchor, self._config.minimum_setpoint, self._config.maximum_setpoint)
        new_minimum_setpoint = round(factor * old_minimum_setpoint + (1.0 - factor) * anchor, 1)
        new_minimum_setpoint = clamp(new_minimum_setpoint, self._config.minimum_setpoint, self._config.maximum_setpoint)

        regime_state.minimum_setpoint = new_minimum_setpoint

        _LOGGER.debug(
            "Relaxing regime %s minimum toward anchor=%.1f: %.1f -> %.1f (factor=%.2f, ran_near_minimum=%s, anchor_source=%s)",
            self._active_regime_key, anchor, old_minimum_setpoint, new_minimum_setpoint, factor, ran_near_minimum, anchor_source
        )

    def _make_regime_key(self, cycles: "CycleStatistics", requested_setpoint: float, outside_temperature: Optional[float]) -> str:
        setpoint_band = self._bucket_setpoint_band_with_hysteresis(requested_setpoint)
        temperature_band = self._bucket_outside_temperature_with_hysteresis(outside_temperature)
        delta_bucket = self._bucket_delta_with_hysteresis(cycles.delta_flow_minus_return_p50_4h)

        return f"{setpoint_band}:{temperature_band}:{delta_bucket}"

    def _bucket_setpoint_band_with_hysteresis(self, requested_setpoint: float) -> int:
        band_width = float(self._config.regime_band_width)
        raw_band = int((requested_setpoint + (band_width / 2.0)) // band_width)

        previous_band = self._previous_setpoint_band
        if previous_band is None:
            self._previous_setpoint_band = raw_band
            return raw_band

        # Thresholds
        margin = band_width * 0.25
        previous_center = previous_band * band_width
        upper_boundary = previous_center + (band_width / 2.0) + margin
        lower_boundary = previous_center - (band_width / 2.0) - margin

        band = previous_band
        if requested_setpoint >= upper_boundary:
            band = raw_band
        elif requested_setpoint <= lower_boundary:
            band = raw_band

        self._previous_setpoint_band = band
        return band

    def _bucket_outside_temperature_with_hysteresis(self, outside_temperature: Optional[float]) -> str:
        if outside_temperature is None:
            self._previous_outside_temperature_bucket = "unknown"
            return self._previous_outside_temperature_bucket

        previous_bucket = self._previous_outside_temperature_bucket

        # Thresholds
        margin = 0.5
        cold_threshold = 5.0
        mild_threshold = 15.0
        freezing_threshold = 0.0

        def initial_bucket(value: float) -> str:
            if value < freezing_threshold:
                return "freezing"
            if value < cold_threshold:
                return "cold"
            if value < mild_threshold:
                return "mild"

            return "warm"

        if previous_bucket is None:
            bucket = initial_bucket(outside_temperature)
            self._previous_outside_temperature_bucket = bucket
            return bucket

        if previous_bucket == "freezing":
            if outside_temperature >= freezing_threshold + margin:
                previous_bucket = "cold"

        elif previous_bucket == "cold":
            if outside_temperature < freezing_threshold - margin:
                previous_bucket = "freezing"
            elif outside_temperature >= cold_threshold + margin:
                previous_bucket = "mild"

        elif previous_bucket == "mild":
            if outside_temperature < cold_threshold - margin:
                previous_bucket = "cold"
            elif outside_temperature >= mild_threshold + margin:
                previous_bucket = "warm"

        elif previous_bucket == "warm":
            if outside_temperature < mild_threshold - margin:
                previous_bucket = "mild"

        self._previous_outside_temperature_bucket = previous_bucket
        return previous_bucket

    def _bucket_delta_with_hysteresis(self, delta: Optional[float]) -> str:
        if delta is None:
            self._previous_delta_bucket = "d_unknown"
            return "d_unknown"

        previous = self._previous_delta_bucket

        # Thresholds
        margin = 1.0
        thresholds = [5.0, 10.0, 15.0]

        def raw_bucket(value: float) -> str:
            if value < thresholds[0]:
                return "d_vlow"
            if value < thresholds[1]:
                return "d_low"
            if value < thresholds[2]:
                return "d_med"
            return "d_high"

        if previous is None:
            bucket = raw_bucket(delta)
            self._previous_delta_bucket = bucket
            return bucket

        if previous == "d_vlow" and delta >= thresholds[0] + margin:
            previous = "d_low"
        elif previous == "d_low":
            if delta < thresholds[0] - margin:
                previous = "d_vlow"
            elif delta >= thresholds[1] + margin:
                previous = "d_med"
        elif previous == "d_med":
            if delta < thresholds[1] - margin:
                previous = "d_low"
            elif delta >= thresholds[2] + margin:
                previous = "d_high"
        elif previous == "d_high" and delta < thresholds[2] - margin:
            previous = "d_med"

        self._previous_delta_bucket = previous
        return previous
