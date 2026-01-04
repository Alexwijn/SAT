from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .boiler import BoilerControlIntent, BoilerCapabilities
from .coordinator import ControlLoopSample
from .cycles import CycleKind, TARGET_MIN_ON_TIME_SECONDS, ULTRA_SHORT_MIN_ON_TIME_SECONDS
from .helpers import clamp
from .types import CycleClassification

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
MINIMUM_SETPOINT_LEARNING_BAND: float = 3.0

# Offset decay factors in various cases
MINIMUM_RELAX_FACTOR_WHEN_UNTUNABLE: float = 0.8

# Regime grouping: bucket base setpoint into bands so we can remember different regimes.
REGIME_BAND_WIDTH: float = 3.0

OUTSIDE_BAND_UNKNOWN = "unknown"
OUTSIDE_BAND_FREEZING = "freezing"
OUTSIDE_BAND_COLD = "cold"
OUTSIDE_BAND_MILD = "mild"
OUTSIDE_BAND_WARM = "warm"

OUTSIDE_TEMP_MARGIN: float = 0.5
OUTSIDE_TEMP_FREEZING_THRESHOLD: float = 0.0
OUTSIDE_TEMP_COLD_THRESHOLD: float = 5.0
OUTSIDE_TEMP_MILD_THRESHOLD: float = 15.0

DELTA_BAND_UNKNOWN = "d_unknown"
DELTA_BAND_VLOW = "d_vlow"
DELTA_BAND_LOW = "d_low"
DELTA_BAND_MED = "d_med"
DELTA_BAND_HIGH = "d_high"
DELTA_BAND_MARGIN: float = 1.0
DELTA_BAND_THRESHOLDS: tuple[float, float, float] = (5.0, 10.0, 15.0)

STORAGE_KEY_VALUE = "value"
STORAGE_KEY_REGIMES = "regimes"
STORAGE_KEY_MINIMUM_SETPOINT = "minimum_setpoint"
STORAGE_KEY_COMPLETED_CYCLES = "completed_cycles"
STORAGE_KEY_STABLE_CYCLES = "stable_cycles"
STORAGE_KEY_LAST_SEEN = "last_seen"

ANCHOR_SOURCE_FLOW_FLOOR = "flow_floor"
ANCHOR_SOURCE_TAIL_SETPOINT = "tail_setpoint"
ANCHOR_SOURCE_INTENT_SETPOINT = "intent_setpoint"

FLOOR_MARGIN: float = 3.0
REGIME_RETENTION_DAYS: int = 90
MIN_STABLE_CYCLES_TO_TRUST: float = 2

MINIMUM_SETPOINT_STEP_MIN: float = 0.3
MINIMUM_SETPOINT_STEP_MAX: float = 1.5
MAX_MINIMUM_SETPOINT_INCREASE_PER_DAY: float = 2.0
MINIMUM_SETPOINT_INCREASE_COOLDOWN = timedelta(hours=2)

LOAD_DROP_FLOW_RETURN_DELTA_THRESHOLD: float = 20.0
LOAD_DROP_FLOW_RETURN_DELTA_FRACTION: float = 0.35

CONDENSING_STEP_BASE: float = 0.2
CONDENSING_STEP_SCALE: float = 0.02
CONDENSING_STEP_FALLBACK: float = 0.2
CONDENSING_RETURN_TEMP_TARGET: float = 55.0


@dataclass(frozen=True, slots=True)
class MinimumSetpointConfig:
    minimum_setpoint: float
    maximum_setpoint: float


@dataclass(slots=True)
class RegimeState:
    key: RegimeKey
    minimum_setpoint: float

    stable_cycles: int = 0
    completed_cycles: int = 0
    last_seen: Optional[datetime] = None
    last_increase_at: Optional[datetime] = None

    increase_window_total: float = 0.0
    increase_window_start: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class RegimeKey:
    """Value object for regime bucketing."""

    setpoint_band: int
    outside_band: str
    delta_band: str

    def to_storage(self) -> str:
        return f"{self.setpoint_band}:{self.outside_band}:{self.delta_band}"

    @staticmethod
    def from_storage(value: Optional[str]) -> Optional["RegimeKey"]:
        if not value:
            return None

        parts = value.split(":")
        if len(parts) < 3:
            return None

        try:
            setpoint_band = int(parts[0])
        except (TypeError, ValueError):
            return None

        outside_band = parts[1]
        delta_band = parts[2]

        return RegimeKey(setpoint_band=setpoint_band, outside_band=outside_band, delta_band=delta_band)


@dataclass(slots=True)
class RegimeBucketizer:
    """Stateful bucketizer with hysteresis for regime keys."""

    previous_setpoint_band: Optional[int] = None
    previous_delta_bucket: Optional[str] = None
    previous_outside_temperature_bucket: Optional[str] = None

    def make_key(self, boiler_control_intent: BoilerControlIntent, flow_setpoint_error: Optional[float], outside_temperature: Optional[float]) -> RegimeKey:
        setpoint_band = self._bucket_setpoint_band_with_hysteresis(boiler_control_intent)
        delta_bucket = self._bucket_delta_with_hysteresis(flow_setpoint_error)
        outside_band = self._bucket_outside_temperature_with_hysteresis(outside_temperature)

        return RegimeKey(setpoint_band=setpoint_band, outside_band=outside_band, delta_band=delta_bucket)

    def _bucket_setpoint_band_with_hysteresis(self, boiler_control_intent: BoilerControlIntent) -> int:
        raw_band = int((boiler_control_intent.setpoint + (REGIME_BAND_WIDTH / 2.0)) // REGIME_BAND_WIDTH)

        previous_band = self.previous_setpoint_band
        if previous_band is None:
            self.previous_setpoint_band = raw_band
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

        self.previous_setpoint_band = band
        return band

    def _bucket_outside_temperature_with_hysteresis(self, outside_temperature: Optional[float]) -> str:
        previous = self.previous_outside_temperature_bucket

        if outside_temperature is None:
            return previous or OUTSIDE_BAND_UNKNOWN

        # Thresholds
        def initial_bucket(value: float) -> str:
            if value < OUTSIDE_TEMP_FREEZING_THRESHOLD:
                return OUTSIDE_BAND_FREEZING

            if value < OUTSIDE_TEMP_COLD_THRESHOLD:
                return OUTSIDE_BAND_COLD

            if value < OUTSIDE_TEMP_MILD_THRESHOLD:
                return OUTSIDE_BAND_MILD

            return OUTSIDE_BAND_WARM

        if previous is None:
            bucket = initial_bucket(outside_temperature)
            self.previous_outside_temperature_bucket = bucket
            return bucket

        if previous == OUTSIDE_BAND_FREEZING:
            if outside_temperature >= OUTSIDE_TEMP_FREEZING_THRESHOLD + OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_COLD

        elif previous == OUTSIDE_BAND_COLD:
            if outside_temperature < OUTSIDE_TEMP_FREEZING_THRESHOLD - OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_FREEZING

            elif outside_temperature >= OUTSIDE_TEMP_COLD_THRESHOLD + OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_MILD

        elif previous == OUTSIDE_BAND_MILD:
            if outside_temperature < OUTSIDE_TEMP_COLD_THRESHOLD - OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_COLD

            elif outside_temperature >= OUTSIDE_TEMP_MILD_THRESHOLD + OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_WARM

        elif previous == OUTSIDE_BAND_WARM:
            if outside_temperature < OUTSIDE_TEMP_MILD_THRESHOLD - OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_MILD

        self.previous_outside_temperature_bucket = previous
        return previous

    def _bucket_delta_with_hysteresis(self, delta: Optional[float]) -> str:
        previous = self.previous_delta_bucket

        if delta is None:
            return previous or DELTA_BAND_UNKNOWN

        # Thresholds
        def raw_bucket(value: float) -> str:
            if value < DELTA_BAND_THRESHOLDS[0]:
                return DELTA_BAND_VLOW
            if value < DELTA_BAND_THRESHOLDS[1]:
                return DELTA_BAND_LOW
            if value < DELTA_BAND_THRESHOLDS[2]:
                return DELTA_BAND_MED
            return DELTA_BAND_HIGH

        if previous is None:
            bucket = raw_bucket(delta)
            self.previous_delta_bucket = bucket
            return bucket

        if previous == DELTA_BAND_VLOW and delta >= DELTA_BAND_THRESHOLDS[0] + DELTA_BAND_MARGIN:
            previous = DELTA_BAND_LOW
        elif previous == DELTA_BAND_LOW:
            if delta < DELTA_BAND_THRESHOLDS[0] - DELTA_BAND_MARGIN:
                previous = DELTA_BAND_VLOW
            elif delta >= DELTA_BAND_THRESHOLDS[1] + DELTA_BAND_MARGIN:
                previous = DELTA_BAND_MED
        elif previous == DELTA_BAND_MED:
            if delta < DELTA_BAND_THRESHOLDS[1] - DELTA_BAND_MARGIN:
                previous = DELTA_BAND_LOW
            elif delta >= DELTA_BAND_THRESHOLDS[2] + DELTA_BAND_MARGIN:
                previous = DELTA_BAND_HIGH
        elif previous == DELTA_BAND_HIGH and delta < DELTA_BAND_THRESHOLDS[2] - DELTA_BAND_MARGIN:
            previous = DELTA_BAND_MED

        self.previous_delta_bucket = previous
        return previous


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

    @property
    def value(self) -> float:
        return round(self._value if self._value is not None else self._config.minimum_setpoint, 1)

    def reset(self) -> None:
        """Reset learned minimums and internal state."""
        self._value = None
        self._regimes.clear()
        self._active_regime = None

        self._bucketizer = RegimeBucketizer()

    def on_cycle_start(self, boiler_capabilities: BoilerCapabilities, sample: ControlLoopSample) -> None:
        """Initialize or switch regime buckets when a new cycle begins."""
        if sample.intent.setpoint is None:
            return

        active_key = self._make_regime_key(sample)
        now = datetime.now(timezone.utc)

        if (regime_state := self._regimes.get(active_key)) is None:
            regime_state = RegimeState(
                key=active_key,
                minimum_setpoint=self._seed_minimum_for_new_regime(
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
            cycle.classification.name, regime_state.key.to_storage(), regime_state.completed_cycles, regime_state.stable_cycles
        )

        # Track before/after for tuning visibility
        previous_minimum = regime_state.minimum_setpoint
        self._maybe_tune_minimum(boiler_capabilities, cycles, cycle)

        # Clamp learned minimum for this regime to absolute range.
        regime_state.minimum_setpoint = clamp(regime_state.minimum_setpoint, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

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

    def _maybe_tune_minimum(self, boiler_capabilities: BoilerCapabilities, cycles: "CycleStatistics", cycle: Cycle) -> None:
        """Decide whether and how to adjust the learned minimum setpoint for the active regime after a cycle."""
        if self._active_regime is None:
            return

        # Only use cycles that are predominantly space heating.
        if cycle.kind not in (CycleKind.CENTRAL_HEATING, CycleKind.MIXED):
            _LOGGER.debug("Ignoring non-heating cycle kind=%s for tuning.", cycle.kind)
            return

        if cycle.fraction_space_heating < MIN_SPACE_HEATING_FRACTION_FOR_TUNING:
            _LOGGER.debug("Cycle has too little space-heating fraction (%.2f), ignoring.", cycle.fraction_space_heating)
            return

        regime_state = self._active_regime
        classification = cycle.classification

        if classification == CycleClassification.PREMATURE_OFF:
            off_with_demand_minutes = None
            if cycles.window.off_with_demand_duration is not None:
                off_with_demand_minutes = max(0.0, float(cycles.window.off_with_demand_duration) / 60.0)

            step = self._compute_scaled_step(base=0.5, scale=0.1, value=off_with_demand_minutes, fallback=1.0)
            regime_state.minimum_setpoint += step
            _LOGGER.debug("Premature flame off detected; increasing minimum setpoint to %.2f", regime_state.minimum_setpoint)
            return

        # Check if the current regime is suitable for minimum tuning.
        if not self._is_tunable_regime(cycles):
            self._relax_toward_anchor(cycle, boiler_capabilities, MINIMUM_RELAX_FACTOR_WHEN_UNTUNABLE)
            return

        reference_setpoint = cycle.tail.setpoint.p50

        if reference_setpoint is None:
            _LOGGER.debug("No setpoint found for cycle, skipping tuning.")
            return

        current_minimum = regime_state.minimum_setpoint

        if abs(reference_setpoint - current_minimum) > MINIMUM_SETPOINT_LEARNING_BAND:
            _LOGGER.debug("Cycle reference_setpoint=%.1f is too far from regime minimum_setpoint=%.1f (band=%.1f), skipping tuning.", reference_setpoint, current_minimum, MINIMUM_SETPOINT_LEARNING_BAND)
            return

        if classification in (CycleClassification.FAST_OVERSHOOT, CycleClassification.TOO_SHORT_OVERSHOOT):
            is_ultra_short = cycle.duration < ULTRA_SHORT_MIN_ON_TIME_SECONDS
            is_very_short = cycle.duration < (TARGET_MIN_ON_TIME_SECONDS * 0.5)
            is_low_duty = cycles.window.duty_ratio_last_15m <= LOW_LOAD_MAXIMUM_DUTY_RATIO_15_M
            is_frequent_cycles = cycles.window.last_hour_count >= LOW_LOAD_MINIMUM_CYCLES_PER_HOUR

            if is_very_short and is_low_duty and is_frequent_cycles and (not is_ultra_short):
                _LOGGER.debug(
                    "Ignoring %s for minimum tuning under low-load: duration=%.1fs (< %.1fs), duty_15m=%.2f (<= %.2f), cycles_last_hour=%.1f (>= %.1f).",
                    classification.name,
                    cycle.duration,
                    TARGET_MIN_ON_TIME_SECONDS * 0.5,
                    cycles.window.duty_ratio_last_15m,
                    LOW_LOAD_MAXIMUM_DUTY_RATIO_15_M,
                    cycles.window.last_hour_count,
                    LOW_LOAD_MINIMUM_CYCLES_PER_HOUR,
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
        elif classification == CycleClassification.GOOD:
            if regime_state.stable_cycles >= 2:
                regime_state.minimum_setpoint -= 0.3
                _LOGGER.debug("GOOD stable cycle; decreasing minimum setpoint by 0.3.")

            self._apply_condensing_bias(cycle, regime_state)

        # FAST_UNDERHEAT / TOO_SHORT_UNDERHEAT:
        #   - Boiler fails to approach the requested flow temperature.
        #   - Indicates the requested flow setpoint is too high for the available heat output.
        elif classification in (CycleClassification.FAST_UNDERHEAT, CycleClassification.TOO_SHORT_UNDERHEAT):
            regime_state.minimum_setpoint -= 1.0
            _LOGGER.debug("Underheat cycle; decreasing minimum setpoint by 1.0.")

        # FAST_OVERSHOOT / TOO_SHORT_OVERSHOOT:
        #   - Boiler fails to stay stable at the requested flow temperature.
        #   - Indicates the requested flow setpoint is too low for the available heat output.
        elif classification in (CycleClassification.FAST_OVERSHOOT, CycleClassification.TOO_SHORT_OVERSHOOT):
            if self._is_load_drop_overshoot(cycle):
                _LOGGER.debug("Overshoot likely due to load drop; skipping minimum setpoint increase.")
                return

            if (applied_step := self._apply_increase_with_limits(regime_state, step=1.0)) > 0.0:
                _LOGGER.debug("Overshoot cycle; increasing minimum setpoint by %.2f.", applied_step)

        # LONG_UNDERHEAT:
        #   - Long burn, but flow temperature remains below setpoint.
        #   - Indicates chronic underheating at this setpoint.
        elif classification == CycleClassification.LONG_UNDERHEAT:
            error = cycle.tail.flow_setpoint_error.p90
            step = self._compute_scaled_step(base=0.3, scale=0.1, value=abs(error) if error is not None else None, fallback=0.5)

            regime_state.minimum_setpoint -= step
            _LOGGER.debug("Long underheat; decreasing minimum setpoint by %.2f.", step)
            self._apply_condensing_bias(cycle, regime_state)

        # LONG_OVERSHOOT:
        #   - Sustained overshoot during a longer burn.
        #   - More likely indicates the requested flow setpoint is genuinely too low for stable operation.
        elif classification == CycleClassification.LONG_OVERSHOOT:
            if self._is_load_drop_overshoot(cycle):
                _LOGGER.debug("Overshoot likely due to load drop; skipping minimum setpoint increase.")
                return

            error = cycle.tail.flow_setpoint_error.p90
            step = self._compute_scaled_step(base=0.3, scale=0.1, value=abs(error) if error is not None else None, fallback=0.5)

            if (applied_step := self._apply_increase_with_limits(regime_state, step=step)) > 0.0:
                _LOGGER.debug("Long overshoot; increasing minimum setpoint by %.2f.", applied_step)

    def _prune_regimes(self, time: datetime) -> None:
        cutoff = time - timedelta(days=REGIME_RETENTION_DAYS)
        stale_keys = [key for key, state in self._regimes.items() if state.last_seen is not None and state.last_seen < cutoff]

        if not stale_keys:
            return

        for key in stale_keys:
            self._regimes.pop(key, None)

        if self._active_regime is not None and self._active_regime.key in stale_keys:
            self._active_regime = None

    def _relax_toward_anchor(self, cycle: "Cycle", boiler_capabilities: "BoilerCapabilities", factor: float) -> None:
        """Relax the regime minimum toward a stable, outcome-derived anchor."""
        regime_state: RegimeState = self._active_regime
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
            anchor_source = ANCHOR_SOURCE_FLOW_FLOOR
        elif effective_setpoint is not None:
            anchor = effective_setpoint
            anchor_source = ANCHOR_SOURCE_TAIL_SETPOINT
        else:
            anchor = cycle.metrics.intent_setpoint.p90
            anchor_source = ANCHOR_SOURCE_INTENT_SETPOINT

        anchor = clamp(anchor, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)
        new_minimum_setpoint = round(factor * old_minimum_setpoint + (1.0 - factor) * anchor, 1)
        new_minimum_setpoint = clamp(new_minimum_setpoint, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

        regime_state.minimum_setpoint = new_minimum_setpoint

        _LOGGER.debug(
            "Relaxing regime %s minimum toward anchor=%.1f: %.1f -> %.1f (factor=%.2f, ran_near_minimum=%s, anchor_source=%s)",
            regime_state.key.to_storage(),
            anchor,
            old_minimum_setpoint,
            new_minimum_setpoint,
            factor,
            ran_near_minimum,
            anchor_source,
        )

    def _seed_minimum_for_new_regime(self, active_key: RegimeKey, boiler_control_intent: BoilerControlIntent, boiler_capabilities: BoilerCapabilities) -> float:
        if (initial_minimum := self._initial_minimum_for_regime(active_key)) is not None:
            return clamp(initial_minimum, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

        if self._value is not None:
            return clamp(self._value, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

        return clamp(boiler_control_intent.setpoint, boiler_capabilities.minimum_setpoint, boiler_capabilities.maximum_setpoint)

    def _initial_minimum_for_regime(self, active_key: RegimeKey) -> Optional[float]:
        if not self._regimes:
            return None

        temperature_band_order: dict[str, int] = {
            OUTSIDE_BAND_UNKNOWN: 0,
            OUTSIDE_BAND_FREEZING: 1,
            OUTSIDE_BAND_COLD: 2,
            OUTSIDE_BAND_MILD: 3,
            OUTSIDE_BAND_WARM: 4,
        }

        trusted_regimes = {
            key: state
            for key, state in self._regimes.items()
            if (state.stable_cycles >= MIN_STABLE_CYCLES_TO_TRUST) and (state.completed_cycles >= 3)
        }

        if not trusted_regimes:
            return None

        if not (parsed_regimes := list(trusted_regimes.items())):
            return None

        def regime_distance(parsed: RegimeKey) -> tuple[int, int]:
            temperature_a = temperature_band_order.get(parsed.outside_band, 0)
            temperature_b = temperature_band_order.get(active_key.outside_band, 0)
            return abs(parsed.setpoint_band - active_key.setpoint_band), abs(temperature_a - temperature_b)

        _, closest_state = min(parsed_regimes, key=lambda item: regime_distance(item[0]))
        return round(0.7 * self._value + 0.3 * closest_state.minimum_setpoint, 1) if self._value is not None else closest_state.minimum_setpoint

    def _make_regime_key(self, sample: ControlLoopSample) -> RegimeKey:
        """Build a stable key using hysteresis buckets to avoid flapping regimes."""
        key = self._bucketizer.make_key(
            boiler_control_intent=sample.intent,
            flow_setpoint_error=sample.state.flow_setpoint_error,
            outside_temperature=sample.outside_temperature,
        )

        return key

    @staticmethod
    def _is_load_drop_overshoot(cycle: Cycle) -> bool:
        if (delta := cycle.tail.flow_return_delta.p90) is None:
            return False

        flow_temp = cycle.tail.flow_temperature.p90
        dynamic_threshold = LOAD_DROP_FLOW_RETURN_DELTA_THRESHOLD

        # Scale threshold by flow temp to avoid treating high-ΔT systems as load-drop overshoot.
        if flow_temp is not None:
            dynamic_threshold = max(dynamic_threshold, flow_temp * LOAD_DROP_FLOW_RETURN_DELTA_FRACTION)

        return delta >= dynamic_threshold

    @staticmethod
    def _apply_condensing_bias(cycle: Cycle, regime_state: RegimeState) -> None:
        # Only bias downward when return temps are high, to encourage condensing efficiency.
        if cycle.tail.return_temperature.p90 is None or cycle.tail.return_temperature.p90 <= CONDENSING_RETURN_TEMP_TARGET:
            return

        overshoot = cycle.tail.return_temperature.p90 - CONDENSING_RETURN_TEMP_TARGET
        step = DynamicMinimumSetpoint._compute_scaled_step(
            base=CONDENSING_STEP_BASE,
            scale=CONDENSING_STEP_SCALE,
            value=overshoot,
            fallback=CONDENSING_STEP_FALLBACK,
        )

        regime_state.minimum_setpoint -= step

        _LOGGER.debug(
            "Condensing bias applied: return_temperature=%.1f°C target=%.1f°C step=%.2f.",
            cycle.tail.return_temperature.p90, CONDENSING_RETURN_TEMP_TARGET, step,
        )

    @staticmethod
    def _apply_increase_with_limits(regime_state: RegimeState, step: float) -> float:
        if step <= 0.0:
            return 0.0

        now = datetime.now(timezone.utc)

        # Cooldown prevents rapid increases from a single noisy regime.
        if regime_state.last_increase_at is not None and (now - regime_state.last_increase_at) < MINIMUM_SETPOINT_INCREASE_COOLDOWN:
            _LOGGER.debug("Skipping minimum setpoint increase due to cooldown.")
            return 0.0

        window_start = regime_state.increase_window_start
        if window_start is None or (now - window_start) >= timedelta(days=1):
            regime_state.increase_window_start = now
            regime_state.increase_window_total = 0.0

        remaining = MAX_MINIMUM_SETPOINT_INCREASE_PER_DAY - regime_state.increase_window_total
        # Daily cap stops slow drift upward due to persistent but non-actionable overshoot.
        if remaining <= 0.0:
            _LOGGER.debug("Daily minimum setpoint increase limit reached; skipping increase.")
            return 0.0

        applied_step = min(step, remaining)
        regime_state.minimum_setpoint += applied_step
        regime_state.increase_window_total += applied_step
        regime_state.last_increase_at = now

        _LOGGER.debug(
            "Applied minimum setpoint increase: step=%.2f remaining_today=%.2f.",
            applied_step, MAX_MINIMUM_SETPOINT_INCREASE_PER_DAY - regime_state.increase_window_total,
        )

        return applied_step

    @staticmethod
    def _compute_scaled_step(base: float, scale: float, value: Optional[float], fallback: float) -> float:
        """Compute a clamped step size from a linear scale."""
        if value is None:
            return fallback

        return clamp(base + (scale * value), MINIMUM_SETPOINT_STEP_MIN, MINIMUM_SETPOINT_STEP_MAX)

    @staticmethod
    def _parse_last_seen(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None

        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _is_tunable_regime(cycles: CycleStatistics) -> bool:
        """Decide whether the current conditions are suitable for minimum tuning."""
        if cycles.window.sample_count_4h < MINIMUM_ON_SAMPLES_FOR_TUNING:
            return False

        if cycles.window.last_hour_count < LOW_LOAD_MINIMUM_CYCLES_PER_HOUR:
            return False

        return True
