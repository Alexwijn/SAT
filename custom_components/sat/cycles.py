from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING, Callable, Deque, Optional, TypeAlias

from homeassistant.core import HomeAssistant

from .const import CycleClassification, EVENT_SAT_CYCLE_ENDED, EVENT_SAT_CYCLE_STARTED, COLD_SETPOINT
from .helpers import clamp, min_max, percentile_interpolated, seconds_since
from .types import Percentiles, CycleKind, PWMStatus

if TYPE_CHECKING:
    from .pwm import PWMState
    from .boiler import BoilerState
    from .coordinator import ControlLoopSample

_LOGGER = logging.getLogger(__name__)

IN_BAND_MARGIN_CELSIUS: float = 1.0
MAX_ON_DURATION_SECONDS_FOR_ROLLING_WINDOWS: float = 1800.0

# Below this, if we overshoot / underheat, we call it "too short".
TARGET_MIN_ON_TIME_SECONDS: float = 600.0
ULTRA_SHORT_MIN_ON_TIME_SECONDS: float = 90.0

# Flow vs. setpoint classification margins
OVERSHOOT_MARGIN_CELSIUS: float = 2.0
UNDERSHOOT_MARGIN_CELSIUS: float = 2.0
OVERSHOOT_SUSTAIN_SECONDS: float = 60.0

# Timeouts
LAST_CYCLE_MAX_AGE_SECONDS: float = 6 * 3600

# Cycle history windows
DEFAULT_DUTY_WINDOW_SECONDS: int = 15 * 60
DEFAULT_CYCLES_WINDOW_SECONDS: int = 60 * 60
DEFAULT_MEDIAN_WINDOW_SECONDS: int = 4 * 60 * 60


@dataclass(frozen=True, slots=True)
class CycleShapeMetrics:
    """Shape metrics describing how a cycle behaved over time (beyond tail classification)."""
    time_in_band_seconds: float
    time_to_first_overshoot_seconds: Optional[float]
    time_to_sustained_overshoot_seconds: Optional[float]
    total_overshoot_seconds: float
    max_flow_setpoint_error: Optional[float]


@dataclass(frozen=True, slots=True)
class CycleMetrics:
    """Summary percentile statistics for cycle values."""
    setpoint: Percentiles
    intent_setpoint: Percentiles
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
    classification: CycleClassification

    end: float
    start: float
    sample_count: int

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


ControlSampleValueGetter: TypeAlias = Callable[["ControlLoopSample"], Optional[float]]


class CycleHistory:
    """Rolling history of completed flame cycles for statistical analysis."""

    def __init__(self) -> None:
        self._cycle_durations_window: Deque[tuple[float, float]] = deque()
        self._delta_window: Deque[tuple[float, Optional[float], Optional[float]]] = deque()

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
            sum(1 for end_time, _ in self._cycle_durations_window if end_time >= cutoff)
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
        on_seconds = sum(duration_seconds for end_time, duration_seconds in self._cycle_durations_window if end_time >= cutoff)

        if on_seconds <= 0.0:
            return 0.0

        ratio = on_seconds / DEFAULT_DUTY_WINDOW_SECONDS
        return clamp(ratio, 0.0, 1.0)

    @property
    def median_on_duration_seconds_4h(self) -> Optional[float]:
        """Median ON duration of completed cycles in the median window."""
        if not self._cycle_durations_window:
            return None

        durations = [duration_seconds for _, duration_seconds in self._cycle_durations_window]
        return float(median(durations))

    @property
    def flow_return_delta_p50_4h(self) -> Optional[float]:
        values = [value for _, value, _ in self._delta_window if value is not None]
        return percentile_interpolated(values, 0.50)

    @property
    def flow_return_delta_p90_4h(self) -> Optional[float]:
        values = [value for _, value, _ in self._delta_window if value is not None]
        return percentile_interpolated(values, 0.90)

    @property
    def flow_setpoint_error_p50_4h(self) -> Optional[float]:
        values = [value for _, _, value in self._delta_window if value is not None]
        return percentile_interpolated(values, 0.50)

    @property
    def flow_setpoint_error_p90_4h(self) -> Optional[float]:
        values = [value for _, _, value in self._delta_window if value is not None]
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
                sample_count_4h=self.sample_count_4h,
                last_hour_count=self.cycles_last_hour,
                duty_ratio_last_15m=self.duty_ratio_last_15m,
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

        self._cycle_durations_window.append((end_time, capped_duration_seconds))
        self._delta_window.append((end_time, cycle.metrics.flow_return_delta.p50, cycle.metrics.flow_setpoint_error.p50))

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
            latest_times.append(self._cycle_durations_window[-1][0])

        if self._delta_window:
            latest_times.append(self._delta_window[-1][0])

        return max(latest_times) if latest_times else None

    def _prune_cycle_window(self, now: float) -> None:
        """Drop ON-duration samples older than the median window."""
        cutoff = now - DEFAULT_MEDIAN_WINDOW_SECONDS
        while self._cycle_durations_window and self._cycle_durations_window[0][0] < cutoff:
            self._cycle_durations_window.popleft()

    def _prune_delta_window(self, now: float) -> None:
        """Drop delta samples older than the median window."""
        cutoff = now - DEFAULT_MEDIAN_WINDOW_SECONDS
        while self._delta_window and self._delta_window[0][0] < cutoff:
            self._delta_window.popleft()


class CycleTracker:
    """Track ongoing cycles and emit completed Cycle snapshots."""

    def __init__(self, hass: HomeAssistant, history: CycleHistory, minimum_samples_per_cycle: int = 3) -> None:
        if minimum_samples_per_cycle < 1:
            raise ValueError("minimum_samples_per_cycle must be >= 1")

        self._hass = hass
        self._history = history
        self._current_samples: Deque[ControlLoopSample] = deque()
        self._minimum_samples_per_cycle = minimum_samples_per_cycle

        self._last_flame_active: Optional[bool] = None
        self._current_cycle_start: Optional[float] = None
        self._last_flame_off_timestamp: Optional[float] = None

    @property
    def started_since(self) -> Optional[float]:
        return self._current_cycle_start

    def reset(self) -> None:
        """Reset the internal tracking state (history is preserved)."""
        self._current_samples.clear()
        self._last_flame_active = None
        self._current_cycle_start = None
        self._last_flame_off_timestamp = None

    def update(self, sample: ControlLoopSample) -> None:
        """Consume a new control-loop sample to update cycle tracking."""
        previously_active = self._last_flame_active
        currently_active = sample.state.flame_active

        if currently_active and not previously_active:
            self._history.record_off_with_demand_duration(self._compute_off_with_demand_duration(
                timestamp=sample.timestamp,
                state=sample.state,
            ))

            _LOGGER.debug("Flame transition OFF->ON, starting new cycle.")
            self._current_cycle_start = sample.timestamp
            self._current_samples.clear()
            self._hass.bus.fire(EVENT_SAT_CYCLE_STARTED, {"sample": sample})

        if currently_active:
            self._current_samples.append(sample)

        if (not currently_active) and previously_active:
            _LOGGER.debug("Flame transition ON->OFF, finalizing cycle.")
            self._last_flame_off_timestamp = sample.timestamp

            cycle = self._build_cycle_state(sample.state, sample.pwm, end_time=sample.timestamp)
            if cycle is not None:
                self._history.record_cycle(cycle)
                self._hass.bus.fire(EVENT_SAT_CYCLE_ENDED, {"cycle": cycle, "sample": sample})

            self._current_samples.clear()
            self._current_cycle_start = None

        self._last_flame_active = currently_active

    def _compute_off_with_demand_duration(self, state: BoilerState, timestamp: float) -> Optional[float]:
        """Compute OFF duration since the last flame OFF, but only if demand was present."""
        if self._last_flame_off_timestamp is None:
            return None

        off_duration_seconds = max(0.0, timestamp - self._last_flame_off_timestamp)

        demand_present = (
                (not state.hot_water_active)
                and state.central_heating
                and (state.setpoint is not None)
                and (state.flow_temperature is not None)
                and (state.setpoint > state.flow_temperature)
        )

        if demand_present:
            return off_duration_seconds

        self._last_flame_off_timestamp = None
        return None

    def _build_cycle_state(self, boiler_state: BoilerState, pwm_state: PWMState, end_time: float) -> Optional[Cycle]:
        """Build a Cycle from the current sample buffer."""
        if self._current_cycle_start is None:
            _LOGGER.debug("No start time, ignoring cycle.")
            return None

        start_time = self._current_cycle_start
        duration_seconds = max(0.0, end_time - start_time)
        sample_count = len(self._current_samples)

        if sample_count < self._minimum_samples_per_cycle:
            _LOGGER.debug("Too few samples (%d < %d), ignoring cycle.", sample_count, self._minimum_samples_per_cycle)
            return None

        samples = list(self._current_samples)
        dhw_count = sum(1 for sample in samples if sample.state.hot_water_active)
        heating_count = sum(1 for sample in samples if (not sample.state.hot_water_active) and sample.state.central_heating)

        fraction_domestic_hot_water = dhw_count / float(sample_count)
        fraction_space_heating = heating_count / float(sample_count)
        kind = self._determine_cycle_kind(fraction_domestic_hot_water, fraction_space_heating)

        flow_temperatures = [sample.state.flow_temperature for sample in samples]

        _, max_flow_temperature = min_max(flow_temperatures)

        duration = max(0.0, end_time - start_time)
        effective_warmup = min(120.0, duration * 0.25)
        observation_start = start_time + effective_warmup
        tail_start = max(observation_start, end_time - 180.0)

        base_metrics = self._build_metrics(samples=samples)
        tail_metrics = self._build_metrics(samples=samples, tail_start=tail_start)
        shape_metrics = self._build_cycle_shape_metrics(samples=samples, observation_start=observation_start, start_time=start_time)
        classification = self._classify_cycle(
            duration=duration_seconds,
            kind=kind,
            pwm_state=pwm_state,
            boiler_state=boiler_state,
            tail_metrics=tail_metrics,
        )

        return Cycle(
            kind=kind,
            tail=tail_metrics,
            shape=shape_metrics,
            metrics=base_metrics,
            classification=classification,

            end=end_time,
            start=start_time,
            sample_count=sample_count,

            max_flow_temperature=max_flow_temperature,

            fraction_space_heating=fraction_space_heating,
            fraction_domestic_hot_water=fraction_domestic_hot_water,
        )

    @staticmethod
    def _build_metrics(samples: list[ControlLoopSample], tail_start: Optional[float] = None) -> CycleMetrics:
        """Compute percentile metrics for full and tail sections of a cycle."""

        relevant_samples = [
            sample
            for sample in samples
            if tail_start is None or sample.timestamp >= tail_start
        ]

        def tail_values(value_getter: ControlSampleValueGetter) -> list[float]:
            values: list[float] = []
            for sample in relevant_samples:
                value = value_getter(sample)
                if value is not None:
                    values.append(float(value))

            return values

        def tail_percentile(value_getter: ControlSampleValueGetter, quantile: float) -> Optional[float]:
            return percentile_interpolated(tail_values(value_getter), quantile)

        def build_percentiles(value_getter: ControlSampleValueGetter) -> Percentiles:
            return Percentiles(
                p50=tail_percentile(value_getter, 0.50),
                p90=tail_percentile(value_getter, 0.90)
            )

        def get_state_setpoint(sample: ControlLoopSample) -> Optional[float]:
            return sample.state.setpoint

        def get_intent_setpoint(sample: ControlLoopSample) -> Optional[float]:
            return sample.intent.setpoint

        def get_flow_temperature(sample: ControlLoopSample) -> Optional[float]:
            return sample.state.flow_temperature

        def get_return_temperature(sample: ControlLoopSample) -> Optional[float]:
            return sample.state.return_temperature

        def get_flow_setpoint_error(sample: ControlLoopSample) -> Optional[float]:
            return sample.state.flow_setpoint_error

        def get_flow_return_delta(sample: ControlLoopSample) -> Optional[float]:
            return sample.state.flow_return_delta

        def get_relative_modulation_level(sample: ControlLoopSample) -> Optional[float]:
            return sample.state.relative_modulation_level

        hot_water_active_fraction = 0.0
        if relevant_samples:
            hot_water_active_fraction = sum(1 for sample in relevant_samples if sample.state.hot_water_active) / float(len(relevant_samples))

        return CycleMetrics(
            setpoint=build_percentiles(get_state_setpoint),
            intent_setpoint=build_percentiles(get_intent_setpoint),
            flow_temperature=build_percentiles(get_flow_temperature),
            return_temperature=build_percentiles(get_return_temperature),
            relative_modulation_level=build_percentiles(get_relative_modulation_level),
            flow_setpoint_error=build_percentiles(get_flow_setpoint_error),
            flow_return_delta=build_percentiles(get_flow_return_delta),
            hot_water_active_fraction=hot_water_active_fraction,
        )

    @staticmethod
    def _build_cycle_shape_metrics(samples: list[ControlLoopSample], observation_start: float, start_time: float) -> CycleShapeMetrics:
        """Compute shape metrics using flow_setpoint_error over time."""
        relevant_samples = [sample for sample in samples if sample.timestamp >= observation_start]
        if len(relevant_samples) < 2:
            return CycleShapeMetrics(
                time_in_band_seconds=0.0,
                time_to_first_overshoot_seconds=None,
                time_to_sustained_overshoot_seconds=None,
                total_overshoot_seconds=0.0,
                max_flow_setpoint_error=None,
            )

        time_in_band_seconds = 0.0
        total_overshoot_seconds = 0.0

        time_to_first_overshoot_seconds: Optional[float] = None
        time_to_sustained_overshoot_seconds: Optional[float] = None

        current_overshoot_streak_seconds = 0.0
        max_flow_setpoint_error: Optional[float] = None

        for index in range(len(relevant_samples) - 1):
            current_sample = relevant_samples[index]
            next_sample = relevant_samples[index + 1]

            interval_seconds = max(0.0, next_sample.timestamp - current_sample.timestamp)
            flow_setpoint_error = current_sample.state.flow_setpoint_error

            if flow_setpoint_error is None:
                # No contribution when signal is missing.
                current_overshoot_streak_seconds = 0.0
                continue

            if (max_flow_setpoint_error is None) or (flow_setpoint_error > max_flow_setpoint_error):
                max_flow_setpoint_error = flow_setpoint_error

            in_band = abs(flow_setpoint_error) <= IN_BAND_MARGIN_CELSIUS
            is_overshoot = flow_setpoint_error >= OVERSHOOT_MARGIN_CELSIUS

            if in_band:
                time_in_band_seconds += interval_seconds

            if is_overshoot:
                total_overshoot_seconds += interval_seconds

                if time_to_first_overshoot_seconds is None:
                    time_to_first_overshoot_seconds = max(0.0, current_sample.timestamp - start_time)

                current_overshoot_streak_seconds += interval_seconds
                if time_to_sustained_overshoot_seconds is None and current_overshoot_streak_seconds >= OVERSHOOT_SUSTAIN_SECONDS:
                    time_to_sustained_overshoot_seconds = max(0.0, current_sample.timestamp - start_time)
            else:
                current_overshoot_streak_seconds = 0.0

        return CycleShapeMetrics(
            time_in_band_seconds=time_in_band_seconds,
            time_to_first_overshoot_seconds=time_to_first_overshoot_seconds,
            time_to_sustained_overshoot_seconds=time_to_sustained_overshoot_seconds,
            total_overshoot_seconds=total_overshoot_seconds,
            max_flow_setpoint_error=max_flow_setpoint_error,
        )

    @staticmethod
    def _determine_cycle_kind(fraction_domestic_hot_water: float, fraction_space_heating: float) -> CycleKind:
        if fraction_domestic_hot_water > 0.8 and fraction_space_heating < 0.2:
            return CycleKind.DOMESTIC_HOT_WATER

        if fraction_space_heating > 0.8 and fraction_domestic_hot_water < 0.2:
            return CycleKind.CENTRAL_HEATING

        if fraction_domestic_hot_water > 0.1 and fraction_space_heating > 0.1:
            return CycleKind.MIXED

        return CycleKind.UNKNOWN

    @staticmethod
    def _classify_cycle(duration: float, kind: CycleKind, pwm_state: PWMState, boiler_state: BoilerState, tail_metrics: CycleMetrics) -> CycleClassification:
        """Classify a cycle based on duration, PWM state, and tail error metrics."""
        if duration <= 0.0:
            return CycleClassification.INSUFFICIENT_DATA

        if kind in (CycleKind.DOMESTIC_HOT_WATER, CycleKind.UNKNOWN):
            return CycleClassification.UNCERTAIN

        if tail_metrics.hot_water_active_fraction > 0.0:
            return CycleClassification.UNCERTAIN

        def compute_short_threshold_seconds() -> float:
            if pwm_state.status == PWMStatus.IDLE or pwm_state.duty_cycle is None:
                return TARGET_MIN_ON_TIME_SECONDS

            if (on_time_seconds := pwm_state.duty_cycle[0]) is None:
                return TARGET_MIN_ON_TIME_SECONDS

            return float(min(on_time_seconds * 0.9, TARGET_MIN_ON_TIME_SECONDS))

        is_short = duration < compute_short_threshold_seconds()
        is_ultra_short = duration < ULTRA_SHORT_MIN_ON_TIME_SECONDS

        if tail_metrics.flow_setpoint_error.p90 is None:
            return CycleClassification.UNCERTAIN

        overshoot = tail_metrics.flow_setpoint_error.p90 >= OVERSHOOT_MARGIN_CELSIUS
        underheat = tail_metrics.flow_setpoint_error.p90 <= -UNDERSHOOT_MARGIN_CELSIUS

        if underheat and boiler_state.setpoint < COLD_SETPOINT:
            return CycleClassification.UNCERTAIN

        if is_ultra_short:
            if overshoot:
                return CycleClassification.FAST_OVERSHOOT

            if underheat:
                return CycleClassification.FAST_UNDERHEAT

        if is_short:
            if overshoot:
                return CycleClassification.TOO_SHORT_OVERSHOOT

            if underheat:
                return CycleClassification.TOO_SHORT_UNDERHEAT

        if underheat:
            return CycleClassification.LONG_UNDERHEAT

        if overshoot:
            return CycleClassification.LONG_OVERSHOOT

        if pwm_state.status == PWMStatus.ON:
            return CycleClassification.PREMATURE_OFF

        return CycleClassification.GOOD
