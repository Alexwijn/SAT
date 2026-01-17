from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..const import CycleClassification
from ..types import CycleControlMode, CycleKind, Percentiles


@dataclass(frozen=True, slots=True)
class CycleShapeMetrics:
    """Shape metrics describing how a cycle behaved over time (beyond tail classification)."""
    total_overshoot_seconds: float
    max_flow_setpoint_error: Optional[float]

    time_in_band_seconds: float
    time_to_first_overshoot_seconds: Optional[float]
    time_to_sustained_overshoot_seconds: Optional[float]


@dataclass(frozen=True, slots=True)
class CycleMetrics:
    """Summary percentile statistics for cycle values."""
    control_setpoint: Percentiles
    flow_temperature: Percentiles
    return_temperature: Percentiles
    relative_modulation_level: Percentiles

    flow_return_delta: Percentiles
    flow_setpoint_error: Percentiles

    hot_water_active_fraction: float


@dataclass(frozen=True, slots=True)
class Cycle:
    """Completed boiler cycle with classification and metrics."""
    kind: CycleKind
    tail: CycleMetrics
    metrics: CycleMetrics
    shape: CycleShapeMetrics
    control_mode: CycleControlMode
    classification: CycleClassification

    end: float
    start: float
    sample_count: int

    min_flow_temperature: Optional[float]
    max_flow_temperature: Optional[float]

    fraction_space_heating: float
    fraction_domestic_hot_water: float

    @property
    def duration(self) -> float:
        """Computed cycle duration in seconds."""
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class CycleStatistics:
    """Rolling statistics derived from recent completed cycles."""
    window: "CycleWindowStats"
    flow_return_delta: Percentiles
    flow_setpoint_error: Percentiles


@dataclass(frozen=True, slots=True)
class CycleWindowStats:
    """Grouped cycle-rate and duty metrics over recent windows."""
    sample_count_4h: int
    last_hour_count: float
    duty_ratio_last_15m: float
    off_with_demand_duration: Optional[float]
    median_on_duration_seconds_4h: Optional[float]
