from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Deque, Optional

from .const import *
from .types import Cycle, CycleStatistics, CycleWindowStats
from ..helpers import clamp, percentile_interpolated, seconds_since
from ..types import Percentiles

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CycleDeltaSample:
    end_time: float
    flow_return_delta: Optional[float]
    flow_setpoint_error: Optional[float]


@dataclass(frozen=True, slots=True)
class CycleDurationSample:
    duration_seconds: float
    end_time: float


class CycleHistory:
    """Rolling history of completed flame cycles for statistical analysis."""

    def __init__(self) -> None:
        self._delta_window: Deque[CycleDeltaSample] = deque()
        self._cycle_durations_window: Deque[CycleDurationSample] = deque()

        self._last_cycle: Optional[Cycle] = None
        self._off_with_demand_duration: Optional[float] = None

    @property
    def sample_count_4h(self) -> int:
        """Number of ON-duration samples in the median window."""
        return len(self._cycle_durations_window)

    @property
    def cycles_last_hour(self) -> float:
        """Cycles per hour, extrapolated from the cycles window."""
        now = self._current_time_hint()
        cutoff = now - DEFAULT_CYCLES_WINDOW_SECONDS if now is not None else None
        cycle_count = (
            sum(1 for sample in self._cycle_durations_window if sample.end_time >= cutoff)
            if cutoff is not None
            else len(self._cycle_durations_window)
        )

        return cycle_count * 3600.0 / DEFAULT_CYCLES_WINDOW_SECONDS

    @property
    def duty_ratio_last_15m(self) -> float:
        """Duty ratio (0.0–1.0) over the duty window, derived from recorded cycles."""
        now = self._current_time_hint()
        if now is None:
            return 0.0

        cutoff = now - DEFAULT_DUTY_WINDOW_SECONDS
        on_seconds = sum(sample.duration_seconds for sample in self._cycle_durations_window if sample.end_time >= cutoff)

        if on_seconds <= 0.0:
            return 0.0

        ratio = on_seconds / DEFAULT_DUTY_WINDOW_SECONDS
        return clamp(ratio, 0.0, 1.0)

    @property
    def median_on_duration_seconds_4h(self) -> Optional[float]:
        """Median ON duration of completed cycles in the median window."""
        if not self._cycle_durations_window:
            return None

        durations = [sample.duration_seconds for sample in self._cycle_durations_window]
        return float(median(durations))

    @property
    def flow_return_delta_p50_4h(self) -> Optional[float]:
        values = [sample.flow_return_delta for sample in self._delta_window if sample.flow_return_delta is not None]
        return percentile_interpolated(values, 0.50)

    @property
    def flow_return_delta_p90_4h(self) -> Optional[float]:
        values = [sample.flow_return_delta for sample in self._delta_window if sample.flow_return_delta is not None]
        return percentile_interpolated(values, 0.90)

    @property
    def flow_setpoint_error_p50_4h(self) -> Optional[float]:
        values = [sample.flow_setpoint_error for sample in self._delta_window if sample.flow_setpoint_error is not None]
        return percentile_interpolated(values, 0.50)

    @property
    def flow_setpoint_error_p90_4h(self) -> Optional[float]:
        values = [sample.flow_setpoint_error for sample in self._delta_window if sample.flow_setpoint_error is not None]
        return percentile_interpolated(values, 0.90)

    @property
    def last_cycle(self) -> Optional[Cycle]:
        """Most recent completed cycle, or None if it is too old."""
        if self._last_cycle is None:
            return None

        if seconds_since(self._last_cycle.end) > LAST_CYCLE_MAX_AGE_SECONDS:
            return None

        return self._last_cycle

    @property
    def statistics(self) -> CycleStatistics:
        """Snapshot of rolling metrics."""
        return CycleStatistics(
            window=CycleWindowStats(
                last_hour_count=self.cycles_last_hour,
                duty_ratio_last_15m=self.duty_ratio_last_15m,
                sample_count_4h=self.sample_count_4h,
                off_with_demand_duration=self._off_with_demand_duration,
                median_on_duration_seconds_4h=self.median_on_duration_seconds_4h,
            ),
            flow_return_delta=Percentiles(
                p50=self.flow_return_delta_p50_4h,
                p90=self.flow_return_delta_p90_4h,
            ),
            flow_setpoint_error=Percentiles(
                p50=self.flow_setpoint_error_p50_4h,
                p90=self.flow_setpoint_error_p90_4h,
            ),
        )

    def record_cycle(self, cycle: Cycle) -> None:
        """Record a completed flame cycle into rolling windows."""
        end_time = cycle.end
        duration_seconds = max(0.0, cycle.duration)
        capped_duration_seconds = min(duration_seconds, MAX_ON_DURATION_SECONDS_FOR_ROLLING_WINDOWS)

        self._cycle_durations_window.append(CycleDurationSample(
            duration_seconds=capped_duration_seconds,
            end_time=end_time,
        ))
        self._delta_window.append(CycleDeltaSample(
            end_time=end_time,
            flow_return_delta=cycle.metrics.flow_return_delta.p50,
            flow_setpoint_error=cycle.metrics.flow_setpoint_error.p50,
        ))

        self._prune_cycle_window(end_time)
        self._prune_delta_window(end_time)

        self._last_cycle = cycle

        _LOGGER.debug(
            "Recorded cycle kind=%s classification=%s duration=%.1fs capped_duration=%.1fs cycles_last_hour=%.1f samples_4h=%d",
            cycle.kind.name,
            cycle.classification.name,
            duration_seconds,
            capped_duration_seconds,
            self.cycles_last_hour,
            self.sample_count_4h,
        )

        if cycle.shape is not None and cycle.duration >= MAX_ON_DURATION_SECONDS_FOR_ROLLING_WINDOWS:
            _LOGGER.debug(
                "Long cycle shape: duration=%.0fs in_band=%.0fs "
                "t_first_overshoot=%s t_sustained_overshoot=%s "
                "overshoot_total=%.0fs max_error=%.1f°C classification=%s",
                cycle.duration,
                cycle.shape.time_in_band_seconds,
                (
                    f"{cycle.shape.time_to_first_overshoot_seconds:.0f}s"
                    if cycle.shape.time_to_first_overshoot_seconds is not None
                    else "none"
                ),
                (
                    f"{cycle.shape.time_to_sustained_overshoot_seconds:.0f}s"
                    if cycle.shape.time_to_sustained_overshoot_seconds is not None
                    else "none"
                ),
                cycle.shape.total_overshoot_seconds,
                cycle.shape.max_flow_setpoint_error,
                cycle.classification.name,
            )

    def record_off_with_demand_duration(self, duration_seconds: Optional[float]) -> None:
        """Record the OFF-with-demand duration (seconds) measured right before a cycle started."""
        self._off_with_demand_duration = duration_seconds

    def _current_time_hint(self) -> Optional[float]:
        """Return the latest timestamp observed in any rolling window."""
        latest_times: list[float] = []
        if self._cycle_durations_window:
            latest_times.append(self._cycle_durations_window[-1].end_time)

        if self._delta_window:
            latest_times.append(self._delta_window[-1].end_time)

        return max(latest_times) if latest_times else None

    def _prune_cycle_window(self, now: float) -> None:
        """Drop ON-duration samples older than the median window."""
        cutoff = now - DEFAULT_MEDIAN_WINDOW_SECONDS
        while self._cycle_durations_window and self._cycle_durations_window[0].end_time < cutoff:
            self._cycle_durations_window.popleft()

    def _prune_delta_window(self, now: float) -> None:
        """Drop delta samples older than the median window."""
        cutoff = now - DEFAULT_MEDIAN_WINDOW_SECONDS
        while self._delta_window and self._delta_window[0].end_time < cutoff:
            self._delta_window.popleft()
