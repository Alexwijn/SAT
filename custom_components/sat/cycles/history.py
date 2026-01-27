from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Callable, Deque, Optional

from .const import *
from .types import Cycle, CycleStatistics, CycleWindowSnapshot, CycleWindowStatistics, CycleWindowedPercentiles
from ..helpers import clamp, percentile_interpolated, seconds_since
from ..types import CycleClassification, Percentiles

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CycleDeltaSample:
    end_time: float
    flow_return_delta: Optional[float]
    flow_control_setpoint_error: Optional[float]
    flow_requested_setpoint_error: Optional[float]


@dataclass(frozen=True, slots=True)
class CycleDurationSample:
    duration_seconds: float
    end_time: float


@dataclass(frozen=True, slots=True)
class CycleClassificationSample:
    end_time: float
    classification: CycleClassification


class CycleHistory:
    """Rolling history of completed flame cycles for statistical analysis."""

    def __init__(self) -> None:
        self._delta_window: Deque[CycleDeltaSample] = deque()
        self._cycle_durations_window: Deque[CycleDurationSample] = deque()
        self._classification_window: Deque[CycleClassificationSample] = deque()

        self._last_cycle: Optional[Cycle] = None
        self._off_with_demand_duration: Optional[float] = None

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
            window=self.window_statistics,
            flow_return_delta=self.flow_return_delta,
            flow_control_setpoint_error=self.flow_control_setpoint_error,
            flow_requested_setpoint_error=self.flow_requested_setpoint_error,
        )

    @property
    def window_statistics(self) -> CycleWindowStatistics:
        """Snapshot of rolling window statistics."""
        return CycleWindowStatistics(
            recent=self._window_snapshot(DEFAULT_MEDIAN_WINDOW_SECONDS),
            daily=self._window_snapshot(DAILY_WINDOW_SECONDS),
            off_with_demand_duration=self._off_with_demand_duration,
        )

    @property
    def flow_return_delta(self) -> CycleWindowedPercentiles:
        """Snapshot of flow/return delta percentiles over rolling windows."""
        return CycleWindowedPercentiles(
            recent=self._delta_percentiles(DEFAULT_MEDIAN_WINDOW_SECONDS, lambda sample: sample.flow_return_delta),
            daily=self._delta_percentiles(DAILY_WINDOW_SECONDS, lambda sample: sample.flow_return_delta),
        )

    @property
    def flow_control_setpoint_error(self) -> CycleWindowedPercentiles:
        """Snapshot of flow/control setpoint error percentiles over rolling windows."""
        return CycleWindowedPercentiles(
            recent=self._delta_percentiles(DEFAULT_MEDIAN_WINDOW_SECONDS, lambda sample: sample.flow_control_setpoint_error),
            daily=self._delta_percentiles(DAILY_WINDOW_SECONDS, lambda sample: sample.flow_control_setpoint_error),
        )

    @property
    def flow_requested_setpoint_error(self) -> CycleWindowedPercentiles:
        """Snapshot of flow/requested setpoint error percentiles over rolling windows."""
        return CycleWindowedPercentiles(
            recent=self._delta_percentiles(DEFAULT_MEDIAN_WINDOW_SECONDS, lambda sample: sample.flow_requested_setpoint_error),
            daily=self._delta_percentiles(DAILY_WINDOW_SECONDS, lambda sample: sample.flow_requested_setpoint_error),
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
            flow_control_setpoint_error=cycle.metrics.flow_control_setpoint_error.p50,
            flow_requested_setpoint_error=cycle.metrics.flow_requested_setpoint_error.p50,
        ))

        self._classification_window.append(CycleClassificationSample(
            end_time=end_time,
            classification=cycle.classification,
        ))

        self._prune_cycle_window(end_time)
        self._prune_delta_window(end_time)
        self._prune_classification_window(end_time)

        self._last_cycle = cycle

        _LOGGER.debug(
            "Recorded cycle kind=%s classification=%s duration=%.1fs capped_duration=%.1fs samples_4h=%d samples_24h=%d",
            cycle.kind.name,
            cycle.classification.name,
            duration_seconds,
            capped_duration_seconds,
            len(self._recent_duration_samples(DEFAULT_MEDIAN_WINDOW_SECONDS)),
            len(self._recent_duration_samples(DAILY_WINDOW_SECONDS)),
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
                cycle.shape.max_flow_control_setpoint_error,
                cycle.classification.name,
            )

    def record_off_with_demand_duration(self, duration_seconds: Optional[float]) -> None:
        """Record the OFF-with-demand duration (seconds) measured right before a cycle started."""
        self._off_with_demand_duration = duration_seconds

    @staticmethod
    def _classification_fraction(samples: list[CycleClassificationSample], classification: CycleClassification) -> float:
        if not samples:
            return 0.0

        match_count = sum(1 for sample in samples if sample.classification is classification)
        return match_count / len(samples)

    @staticmethod
    def _long_cycle_fraction(samples: list[CycleDurationSample]) -> float:
        if not samples:
            return 0.0

        long_count = sum(1 for sample in samples if sample.duration_seconds >= TARGET_MIN_ON_TIME_SECONDS)
        return long_count / len(samples)

    def _recent_duration_samples(self, window_seconds: int) -> list[CycleDurationSample]:
        now = self._current_time_hint()
        if now is None:
            return []

        cutoff = now - window_seconds
        return [sample for sample in self._cycle_durations_window if sample.end_time >= cutoff]

    def _recent_delta_samples(self, window_seconds: int) -> list[CycleDeltaSample]:
        if (now := self._current_time_hint()) is None:
            return []

        return [sample for sample in self._delta_window if sample.end_time >= now - window_seconds]

    def _recent_classification_samples(self, window_seconds: int) -> list[CycleClassificationSample]:
        if (now := self._current_time_hint()) is None:
            return []

        return [sample for sample in self._classification_window if sample.end_time >= now - window_seconds]

    def _durations_since(self, window_seconds: int) -> list[float]:
        return [sample.duration_seconds for sample in self._recent_duration_samples(window_seconds)]

    def _delta_values_since(self, window_seconds: int, getter: Callable[[CycleDeltaSample], Optional[float]]) -> list[float]:
        return [value for sample in self._recent_delta_samples(window_seconds) if (value := getter(sample)) is not None]

    def _duty_ratio_since(self, window_seconds: int) -> float:
        if (on_seconds := sum(self._durations_since(window_seconds))) <= 0.0:
            return 0.0

        return clamp(on_seconds / window_seconds, 0.0, 1.0)

    def _median_duration_since(self, window_seconds: int) -> Optional[float]:
        if not (durations := self._durations_since(window_seconds)):
            return None

        return float(median(durations))

    def _window_snapshot(self, window_seconds: int) -> CycleWindowSnapshot:
        duration_samples = self._recent_duration_samples(window_seconds)
        classification_samples = self._recent_classification_samples(window_seconds)

        return CycleWindowSnapshot(
            sample_count=len(duration_samples),
            duty_ratio=self._duty_ratio_since(window_seconds),
            long_cycle_fraction=self._long_cycle_fraction(duration_samples),
            median_on_duration_seconds=self._median_duration_since(window_seconds),
            overshoot_fraction=self._classification_fraction(classification_samples, CycleClassification.OVERSHOOT),
            underheat_fraction=self._classification_fraction(classification_samples, CycleClassification.UNDERHEAT),
        )

    def _delta_percentiles(self, window_seconds: int, getter: Callable[[CycleDeltaSample], Optional[float]], ) -> Percentiles:
        values = self._delta_values_since(window_seconds, getter)
        return Percentiles(p50=percentile_interpolated(values, 0.50), p90=percentile_interpolated(values, 0.90))

    def _current_time_hint(self) -> Optional[float]:
        """Return the latest timestamp observed in any rolling window."""
        latest_times: list[float] = []

        if self._delta_window:
            latest_times.append(self._delta_window[-1].end_time)

        if self._classification_window:
            latest_times.append(self._classification_window[-1].end_time)

        if self._cycle_durations_window:
            latest_times.append(self._cycle_durations_window[-1].end_time)

        return max(latest_times) if latest_times else None

    def _prune_delta_window(self, now: float) -> None:
        """Drop delta samples older than the daily window."""
        while self._delta_window and self._delta_window[0].end_time < (now - DAILY_WINDOW_SECONDS):
            self._delta_window.popleft()

    def _prune_cycle_window(self, now: float) -> None:
        """Drop ON-duration samples older than the daily window."""
        while self._cycle_durations_window and self._cycle_durations_window[0].end_time < (now - DAILY_WINDOW_SECONDS):
            self._cycle_durations_window.popleft()

    def _prune_classification_window(self, now: float) -> None:
        """Drop classification samples older than the daily window."""
        while self._classification_window and self._classification_window[0].end_time < (now - DAILY_WINDOW_SECONDS):
            self._classification_window.popleft()
