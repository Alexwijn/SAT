from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

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
MINIMUM_SETPOINT_LEARNING_BAND_EARLY_BONUS: float = 2.0
MINIMUM_SETPOINT_EARLY_TUNING_CYCLES: int = 3
MINIMUM_SETPOINT_EARLY_TUNING_MULTIPLIER: float = 1.3

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
class DeltaBandThresholds:
    low: float
    med: float
    high: float


DELTA_BAND_THRESHOLDS = DeltaBandThresholds(low=5.0, med=10.0, high=15.0)
