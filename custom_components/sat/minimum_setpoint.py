from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .boiler import BoilerControlIntent, BoilerCapabilities
from .coordinator import ControlLoopSample
from .const import CycleClassification
from .cycles import CycleKind, TARGET_MIN_ON_TIME_SECONDS, ULTRA_SHORT_MIN_ON_TIME_SECONDS
from .helpers import clamp

if TYPE_CHECKING:
    from .cycles import CycleStatistics, Cycle

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Low-load detection thresholds (when we care about minimum tuning)
LOW_LOAD_MINIMUM_CYCLES_PER_HOUR: float = 3.0
LOW_LOAD_MAXIMUM_DUTY_RATIO_15_M: float = 0.50

# Minimum samples in history before trusting the low-load regime
MINIMUM_ON_SAMPLES_FOR_TUNING: int = 3

# Minimum fraction of cycle that must be space heating to consider it
MIN_SPACE_HEATING_FRACTION_FOR_TUNING: float = 0.6

# When learning, only trust cycles whose setpoint is close to the current learned minimum.
MINIMUM_SETPOINT_LEARNING_BAND: float = 4.0

# Offset decay factors in various cases
MINIMUM_RELAX_FACTOR_WHEN_UNTUNABLE: float = 0.9

# Regime grouping: bucket base setpoint into bands so we can remember different regimes.
REGIME_BAND_WIDTH: float = 3.0

FLOOR_MARGIN: float = 3.0
MIN_STABLE_CYCLES_TO_TRUST: float = 2


@dataclass(frozen=True, slots=True)
class MinimumSetpointConfig:
    minimum_setpoint: float
    maximum_setpoint: float


@dataclass(slots=True)
class RegimeState:
    minimum_setpoint: float

    stable_cycles: int = 0
    completed_cycles: int = 0


class DynamicMinimumSetpoint:
    def __init__(self, config: MinimumSetpointConfig) -> None:

        self._config: MinimumSetpointConfig = config

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
        return self._value if self._value is not None else self._config.minimum_setpoint

    @property
    def regime_state(self) -> Optional[RegimeState]:
        return self._regimes.get(self._active_regime_key) if self._active_regime_key is not None else None

    def reset(self) -> None:
        """Reset learned minimums and internal state."""
        self._value = None
        self._regimes.clear()
        self._active_regime_key = None

        self._previous_delta_bucket = None
        self._previous_setpoint_band = None
        self._previous_outside_temperature_bucket = None

    def on_cycle_start(self, boiler_capabilities: BoilerCapabilities, sample: ControlLoopSample) -> None:
        if sample.intent.setpoint is None:
            return

        self._active_regime_key = self._make_regime_key(sample)

        if (regime_state := self.regime_state) is None:
            regime_state = RegimeState(minimum_setpoint=self._seed_minimum_for_new_regime(
                boiler_control_intent=sample.intent,
                boiler_capabilities=boiler_capabilities,
            ))

            self._regimes[self._active_regime_key] = regime_state
            _LOGGER.info("Initialized regime %s at cycle start with minimum_setpoint=%.2f", self._active_regime_key, regime_state.minimum_setpoint)

        self._value = regime_state.minimum_setpoint

    def on_cycle_end(self, boiler_capabilities: BoilerCapabilities, cycles: "CycleStatistics", cycle: "Cycle") -> None:
        if (regime_state := self.regime_state) is None:
            return

        _LOGGER.debug("Cycle ended: regime=%s classification=%s", self._active_regime_key, cycle.classification.name)

        # Mark a cycle as completed.
        regime_state.completed_cycles += 1
        _LOGGER.debug("Regime %s completed_cycles=%d", self._active_regime_key, regime_state.completed_cycles)

        # Mark a cycle as stable when the classification is GOOD.
        if cycle.classification == CycleClassification.GOOD:
            regime_state.stable_cycles += 1
            _LOGGER.debug("Regime %s stable cycle detected (stable_cycles=%d)", self._active_regime_key, regime_state.stable_cycles)

        # Track before/after for tuning visibility
        previous_minimum = regime_state.minimum_setpoint
        self._maybe_tune_minimum(boiler_capabilities, cycles, cycle)

        # Clamp learned minimum for this regime to absolute range.
        regime_state.minimum_setpoint = clamp(regime_state.minimum_setpoint, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

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

    @staticmethod
    def _is_tunable_regime(cycles: CycleStatistics) -> bool:
        """Decide whether the current conditions are suitable for minimum tuning."""
        if cycles.sample_count_4h < MINIMUM_ON_SAMPLES_FOR_TUNING:
            return False

        if cycles.last_hour_count < LOW_LOAD_MINIMUM_CYCLES_PER_HOUR:
            return False

        return True

    def _seed_minimum_for_new_regime(self, boiler_control_intent: BoilerControlIntent, boiler_capabilities: BoilerCapabilities) -> float:
        if (initial_minimum := self._initial_minimum_for_regime()) is not None:
            return clamp(initial_minimum, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

        if self._value is not None:
            return clamp(self._value, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

        return clamp(boiler_control_intent.setpoint, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

    def _initial_minimum_for_regime(self) -> Optional[float]:
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
            if (state.stable_cycles >= MIN_STABLE_CYCLES_TO_TRUST) and (state.completed_cycles >= 3)
        }

        if not trusted_regimes:
            return None

        def regime_distance(key: str) -> tuple[int, int, int]:
            parts_a = key.split(":")
            parts_b = self._active_regime_key.split(":")

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

    def _maybe_tune_minimum(self, boiler_capabilities: BoilerCapabilities, cycles: "CycleStatistics", cycle: Cycle) -> None:
        """Decide whether and how to adjust the learned minimum setpoint for the active regime after a cycle."""
        if self._active_regime_key is None:
            return

        # Check if the current regime is suitable for minimum tuning.
        if not self._is_tunable_regime(cycles):
            self._relax_toward_anchor(cycle, boiler_capabilities, MINIMUM_RELAX_FACTOR_WHEN_UNTUNABLE)
            return

        # Only use cycles that are predominantly space heating.
        if cycle.kind not in (CycleKind.CENTRAL_HEATING, CycleKind.MIXED):
            _LOGGER.debug("Ignoring non-heating cycle kind=%s for tuning.", cycle.kind)
            return

        if cycle.fraction_space_heating < MIN_SPACE_HEATING_FRACTION_FOR_TUNING:
            _LOGGER.debug("Cycle has too little space-heating fraction (%.2f), ignoring.", cycle.fraction_space_heating)
            return

        regime_state = self.regime_state
        classification = cycle.classification
        reference_setpoint = cycle.tail.setpoint.p50

        if reference_setpoint is None:
            _LOGGER.debug("No setpoint found for cycle, skipping tuning.")
            return

        current_minimum = regime_state.minimum_setpoint

        if abs(reference_setpoint - current_minimum) > MINIMUM_SETPOINT_LEARNING_BAND:
            _LOGGER.debug(
                "Cycle reference_setpoint=%.1f is too far from regime minimum_setpoint=%.1f (band=%.1f), skipping tuning.",
                reference_setpoint, current_minimum, MINIMUM_SETPOINT_LEARNING_BAND,
            )
            return

        if classification in (CycleClassification.FAST_OVERSHOOT, CycleClassification.TOO_SHORT_OVERSHOOT):
            is_ultra_short = cycle.duration < ULTRA_SHORT_MIN_ON_TIME_SECONDS
            is_very_short = cycle.duration < (TARGET_MIN_ON_TIME_SECONDS * 0.5)
            is_low_duty = cycles.duty_ratio_last_15m <= LOW_LOAD_MAXIMUM_DUTY_RATIO_15_M
            is_frequent_cycles = cycles.last_hour_count >= LOW_LOAD_MINIMUM_CYCLES_PER_HOUR

            if is_very_short and is_low_duty and is_frequent_cycles and (not is_ultra_short):
                _LOGGER.debug(
                    "Ignoring %s for minimum tuning under low-load: duration=%.1fs (< %.1fs), duty_15m=%.2f (<= %.2f), cycles_last_hour=%.1f (>= %.1f).",
                    classification.name, cycle.duration, TARGET_MIN_ON_TIME_SECONDS * 0.5, cycles.duty_ratio_last_15m,
                    LOW_LOAD_MAXIMUM_DUTY_RATIO_15_M, cycles.last_hour_count, LOW_LOAD_MINIMUM_CYCLES_PER_HOUR,
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

    def _relax_toward_anchor(self, cycle: "Cycle", boiler_capabilities: "BoilerCapabilities", factor: float) -> None:
        """Relax the regime minimum toward a stable, outcome-derived anchor."""
        regime_state: RegimeState = self.regime_state
        old_minimum_setpoint: Optional[float] = regime_state.minimum_setpoint

        tail_setpoint_p50: Optional[float] = cycle.tail.setpoint.p50
        tail_setpoint_p90: Optional[float] = cycle.tail.setpoint.p90
        effective_setpoint: Optional[float] = tail_setpoint_p50 if tail_setpoint_p50 is not None else tail_setpoint_p90

        ran_near_minimum = False
        if effective_setpoint is not None:
            ran_near_minimum = (abs(effective_setpoint - old_minimum_setpoint) <= MINIMUM_SETPOINT_LEARNING_BAND)

        anchor_candidate_from_flow: Optional[float] = None
        if ran_near_minimum:
            tail_flow_p50 = cycle.tail.flow_temperature.p50
            tail_flow_p90 = cycle.tail.flow_temperature.p90
            max_flow_temperature = cycle.max_flow_temperature

            flow_reference = (
                tail_flow_p50
                if tail_flow_p50 is not None
                else (tail_flow_p90 if tail_flow_p90 is not None else max_flow_temperature)
            )

            if flow_reference is not None:
                anchor_candidate_from_flow = flow_reference - FLOOR_MARGIN

        if anchor_candidate_from_flow is not None:
            anchor = anchor_candidate_from_flow
            anchor_source = "flow_floor"
        elif effective_setpoint is not None:
            anchor = effective_setpoint
            anchor_source = "tail_setpoint"
        else:
            anchor = cycle.metrics.intent_setpoint.p90
            anchor_source = "intent_setpoint"

        anchor = clamp(anchor, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)
        new_minimum_setpoint = round(factor * old_minimum_setpoint + (1.0 - factor) * anchor, 1)
        new_minimum_setpoint = clamp(new_minimum_setpoint, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

        regime_state.minimum_setpoint = new_minimum_setpoint

        _LOGGER.debug(
            "Relaxing regime %s minimum toward anchor=%.1f: %.1f -> %.1f (factor=%.2f, ran_near_minimum=%s, anchor_source=%s)",
            self._active_regime_key, anchor, old_minimum_setpoint, new_minimum_setpoint, factor, ran_near_minimum, anchor_source
        )

    def _make_regime_key(self, cycle_sample: ControlLoopSample) -> str:
        setpoint_band = self._bucket_setpoint_band_with_hysteresis(cycle_sample.intent)
        delta_bucket = self._bucket_delta_with_hysteresis(cycle_sample.state.flow_setpoint_error)
        outside_band = self._bucket_outside_temperature_with_hysteresis(cycle_sample.outside_temperature)

        return f"{setpoint_band}:{outside_band}:{delta_bucket}"

    def _bucket_setpoint_band_with_hysteresis(self, boiler_control_intent: BoilerControlIntent) -> int:
        raw_band = int((boiler_control_intent.setpoint + (REGIME_BAND_WIDTH / 2.0)) // REGIME_BAND_WIDTH)

        previous_band = self._previous_setpoint_band
        if previous_band is None:
            self._previous_setpoint_band = raw_band
            return raw_band

        # Thresholds
        margin = REGIME_BAND_WIDTH * 0.25
        previous_center = previous_band * REGIME_BAND_WIDTH
        upper_boundary = previous_center + (REGIME_BAND_WIDTH / 2.0) + margin
        lower_boundary = previous_center - (REGIME_BAND_WIDTH / 2.0) - margin

        band = previous_band
        if boiler_control_intent.setpoint >= upper_boundary:
            band = raw_band
        elif boiler_control_intent.setpoint <= lower_boundary:
            band = raw_band

        self._previous_setpoint_band = band
        return band

    def _bucket_outside_temperature_with_hysteresis(self, outside_temperature: Optional[float]) -> str:

        previous = self._previous_outside_temperature_bucket

        if outside_temperature is None:
            return previous or "unknown"

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

        if previous is None:
            bucket = initial_bucket(outside_temperature)
            self._previous_outside_temperature_bucket = bucket
            return bucket

        if previous == "freezing":
            if outside_temperature >= freezing_threshold + margin:
                previous = "cold"

        elif previous == "cold":
            if outside_temperature < freezing_threshold - margin:
                previous = "freezing"

            elif outside_temperature >= cold_threshold + margin:
                previous = "mild"

        elif previous == "mild":
            if outside_temperature < cold_threshold - margin:
                previous = "cold"

            elif outside_temperature >= mild_threshold + margin:
                previous = "warm"

        elif previous == "warm":
            if outside_temperature < mild_threshold - margin:
                previous = "mild"

        self._previous_outside_temperature_bucket = previous
        return previous

    def _bucket_delta_with_hysteresis(self, delta: Optional[float]) -> str:

        previous = self._previous_delta_bucket

        if delta is None:
            return previous or "d_unknown"

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
