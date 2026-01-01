from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from statistics import median
from time import monotonic
from typing import TYPE_CHECKING, Callable, Deque, Optional, TypeAlias

from homeassistant.core import HomeAssistant

from .const import CycleClassification, CycleKind, EVENT_SAT_CYCLE_ENDED, EVENT_SAT_CYCLE_STARTED, PWMStatus, Percentiles
from .helpers import clamp, min_max, percentile_interpolated

if TYPE_CHECKING:
    from .pwm import PWMState
    from .boiler import BoilerState
    from .coordinator import ControlLoopSample

_LOGGER = logging.getLogger(__name__)

# Below this, if we overshoot / underheat, we call it "too short".
TARGET_MIN_ON_TIME_SECONDS: float = 600.0
ULTRA_SHORT_MIN_ON_TIME_SECONDS: float = 90.0

# Flow vs. setpoint classification margins
OVERSHOOT_MARGIN_CELSIUS: float = 2.0
UNDERSHOOT_MARGIN_CELSIUS: float = 2.0

# Timeouts
LAST_CYCLE_MAX_AGE_SECONDS: float = 6 * 3600


@dataclass(frozen=True, slots=True)
class CycleMetrics:
    setpoint: Percentiles
    intent_setpoint: Percentiles
    flow_temperature: Percentiles
    return_temperature: Percentiles
    relative_modulation_level: Percentiles

    flow_return_delta: Percentiles
    flow_setpoint_error: Percentiles


@dataclass(frozen=True, slots=True)
class Cycle:
    kind: CycleKind
    tail: CycleMetrics
    metrics: CycleMetrics
    classification: CycleClassification

    end: float
    start: float
    duration: float
    sample_count: int
    ended_on_phase: bool

    min_setpoint: Optional[float]
    max_setpoint: Optional[float]
    max_flow_temperature: Optional[float]

    fraction_space_heating: float
    fraction_domestic_hot_water: float


@dataclass(frozen=True, slots=True)
class CycleStatistics:
    """Rolling statistics derived from recent completed cycles."""
    sample_count_4h: int
    last_hour_count: float
    duty_ratio_last_15m: float
    off_with_demand_duration: Optional[float]
    median_on_duration_seconds_4h: Optional[float]

    delta_flow_minus_return_p50_4h: Optional[float]
    delta_flow_minus_return_p90_4h: Optional[float]

    delta_flow_minus_setpoint_p50_4h: Optional[float]
    delta_flow_minus_setpoint_p90_4h: Optional[float]


ControlSampleValueGetter: TypeAlias = Callable[["ControlLoopSample"], Optional[float]]


class CycleHistory:
    """Rolling history of completed flame cycles for statistical analysis."""

    def __init__(self, duty_window_seconds: int = 15 * 60, cycles_window_seconds: int = 60 * 60, median_window_seconds: int = 4 * 60 * 60) -> None:
        if duty_window_seconds <= 0:
            raise ValueError("duty_window_seconds must be > 0")

        if cycles_window_seconds <= 0:
            raise ValueError("cycles_window_seconds must be > 0")

        if median_window_seconds <= 0:
            raise ValueError("median_window_seconds must be > 0")

        self._duty_window_seconds = duty_window_seconds
        self._cycles_window_seconds = cycles_window_seconds
        self._median_window_seconds = median_window_seconds

        self._on_durations_window: Deque[tuple[float, float]] = deque()
        self._cycle_end_times_window: Deque[tuple[float, float]] = deque()
        self._delta_flow_minus_return_window: Deque[tuple[float, float]] = deque()
        self._delta_flow_minus_setpoint_window: Deque[tuple[float, float]] = deque()

        self._last_cycle: Optional[Cycle] = None
        self._off_with_demand_duration: Optional[float] = None

    @property
    def sample_count_4h(self) -> int:
        """Number of ON-duration samples in the median window."""
        now = self._current_time_hint()
        if now is not None:
            self._prune_median_window(now)

        return len(self._on_durations_window)

    @property
    def cycles_last_hour(self) -> float:
        """Cycles per hour, extrapolated from the cycles window."""
        now = self._current_time_hint()
        if now is not None:
            self._prune_cycles_window(now)

        cycle_count = len(self._cycle_end_times_window)
        return cycle_count * 3600.0 / float(self._cycles_window_seconds)

    @property
    def duty_ratio_last_15m(self) -> float:
        """Duty ratio (0.0–1.0) over the duty window, derived from recorded cycles."""
        now = self._current_time_hint()
        if now is None:
            return 0.0

        cutoff = now - float(self._duty_window_seconds)
        on_seconds = sum(duration_seconds for end_time, duration_seconds in self._cycle_end_times_window if end_time >= cutoff)

        if on_seconds <= 0.0:
            return 0.0

        ratio = on_seconds / float(self._duty_window_seconds)
        return clamp(ratio, 0.0, 1.0)

    @property
    def median_on_duration_seconds_4h(self) -> Optional[float]:
        """Median ON duration of completed cycles in the median window."""
        now = self._current_time_hint()
        if now is not None:
            self._prune_median_window(now)

        if not self._on_durations_window:
            return None

        durations = [duration_seconds for _, duration_seconds in self._on_durations_window]
        return float(median(durations))

    @property
    def delta_flow_minus_return_p50_4h(self) -> Optional[float]:
        now = self._current_time_hint()
        if now is not None:
            self._prune_delta_flow_minus_return_window(now)

        values = [value for _, value in self._delta_flow_minus_return_window]
        return percentile_interpolated(values, 0.50)

    @property
    def delta_flow_minus_return_p90_4h(self) -> Optional[float]:
        now = self._current_time_hint()
        if now is not None:
            self._prune_delta_flow_minus_return_window(now)

        values = [value for _, value in self._delta_flow_minus_return_window]
        return percentile_interpolated(values, 0.90)

    @property
    def delta_flow_minus_setpoint_p50_4h(self) -> Optional[float]:
        now = self._current_time_hint()
        if now is not None:
            self._prune_delta_flow_minus_setpoint_window(now)

        values = [value for _, value in self._delta_flow_minus_setpoint_window]
        return percentile_interpolated(values, 0.50)

    @property
    def delta_flow_minus_setpoint_p90_4h(self) -> Optional[float]:
        now = self._current_time_hint()
        if now is not None:
            self._prune_delta_flow_minus_setpoint_window(now)

        values = [value for _, value in self._delta_flow_minus_setpoint_window]
        return percentile_interpolated(values, 0.90)

    @property
    def last_cycle(self) -> Optional[Cycle]:
        """Most recent completed cycle, or None if it is too old."""
        if self._last_cycle is None:
            return None

        if (monotonic() - self._last_cycle.end) > LAST_CYCLE_MAX_AGE_SECONDS:
            return None

        return self._last_cycle

    @property
    def statistics(self) -> CycleStatistics:
        """Snapshot of rolling metrics."""
        return CycleStatistics(
            sample_count_4h=self.sample_count_4h,
            last_hour_count=self.cycles_last_hour,
            duty_ratio_last_15m=self.duty_ratio_last_15m,
            off_with_demand_duration=self._off_with_demand_duration,
            median_on_duration_seconds_4h=self.median_on_duration_seconds_4h,

            delta_flow_minus_return_p50_4h=self.delta_flow_minus_return_p50_4h,
            delta_flow_minus_return_p90_4h=self.delta_flow_minus_return_p90_4h,

            delta_flow_minus_setpoint_p50_4h=self.delta_flow_minus_setpoint_p50_4h,
            delta_flow_minus_setpoint_p90_4h=self.delta_flow_minus_setpoint_p90_4h,
        )

    def record_cycle(self, cycle: Cycle) -> None:
        """Record a completed flame cycle into rolling windows."""
        end_time = cycle.end
        duration_seconds = max(0.0, cycle.duration)

        self._on_durations_window.append((end_time, duration_seconds))
        self._cycle_end_times_window.append((end_time, duration_seconds))

        if cycle.metrics.flow_return_delta.p50 is not None:
            self._delta_flow_minus_return_window.append((end_time, cycle.metrics.flow_return_delta.p50))

        if cycle.metrics.flow_setpoint_error.p50 is not None:
            self._delta_flow_minus_setpoint_window.append((end_time, cycle.metrics.flow_setpoint_error.p50))

        self._prune_cycles_window(end_time)
        self._prune_median_window(end_time)

        self._prune_delta_flow_minus_return_window(end_time)
        self._prune_delta_flow_minus_setpoint_window(end_time)

        self._last_cycle = cycle

        _LOGGER.debug(
            "Recorded cycle kind=%s classification=%s duration=%.1fs cycles_last_hour=%.1f samples_4h=%d",
            cycle.kind.name,
            cycle.classification.name,
            duration_seconds,
            self.cycles_last_hour,
            self.sample_count_4h,
        )

    def record_off_with_demand_duration(self, duration_seconds: Optional[float]) -> None:
        """Record the OFF-with-demand duration (seconds) measured right before a cycle started."""
        self._off_with_demand_duration = duration_seconds

    def _current_time_hint(self) -> Optional[float]:
        latest_times: list[float] = []
        if self._cycle_end_times_window:
            latest_times.append(self._cycle_end_times_window[-1][0])

        if self._on_durations_window:
            latest_times.append(self._on_durations_window[-1][0])

        if self._delta_flow_minus_return_window:
            latest_times.append(self._delta_flow_minus_return_window[-1][0])

        if self._delta_flow_minus_setpoint_window:
            latest_times.append(self._delta_flow_minus_setpoint_window[-1][0])

        return max(latest_times) if latest_times else None

    def _prune_cycles_window(self, now: float) -> None:
        cutoff = now - float(self._cycles_window_seconds)
        while self._cycle_end_times_window and self._cycle_end_times_window[0][0] < cutoff:
            self._cycle_end_times_window.popleft()

    def _prune_median_window(self, now: float) -> None:
        cutoff = now - float(self._median_window_seconds)
        while self._on_durations_window and self._on_durations_window[0][0] < cutoff:
            self._on_durations_window.popleft()

    def _prune_delta_flow_minus_return_window(self, now: float) -> None:
        cutoff = now - float(self._median_window_seconds)
        while self._delta_flow_minus_return_window and self._delta_flow_minus_return_window[0][0] < cutoff:
            self._delta_flow_minus_return_window.popleft()

    def _prune_delta_flow_minus_setpoint_window(self, now: float) -> None:
        cutoff = now - float(self._median_window_seconds)
        while self._delta_flow_minus_setpoint_window and self._delta_flow_minus_setpoint_window[0][0] < cutoff:
            self._delta_flow_minus_setpoint_window.popleft()


class CycleTracker:

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

            cycle = self._build_cycle_state(sample.pwm, end_time=sample.timestamp)
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

    def _build_cycle_state(self, pwm: PWMState, end_time: float) -> Optional[Cycle]:
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

        setpoints = [sample.state.setpoint for sample in samples]
        flow_temperatures = [sample.state.flow_temperature for sample in samples]

        min_setpoint, max_setpoint = min_max(setpoints)
        _, max_flow_temperature = min_max(flow_temperatures)

        duration = max(0.0, end_time - start_time)
        effective_warmup = min(120.0, duration * 0.25)
        observation_start = start_time + effective_warmup
        tail_start = max(observation_start, end_time - 180.0)

        base_metrics = self._build_metrics(samples=samples)
        tail_metrics = self._build_metrics(samples=samples, tail_start=tail_start)
        classification = self._classify_cycle(pwm=pwm, duration=duration_seconds, tail_metrics=tail_metrics)

        return Cycle(
            kind=kind,
            tail=tail_metrics,
            metrics=base_metrics,
            classification=classification,
            ended_on_phase=pwm.ended_on_phase,

            end=end_time,
            start=start_time,
            sample_count=sample_count,
            duration=duration_seconds,

            min_setpoint=min_setpoint,
            max_setpoint=max_setpoint,
            max_flow_temperature=max_flow_temperature,

            fraction_space_heating=fraction_space_heating,
            fraction_domestic_hot_water=fraction_domestic_hot_water,
        )

    @staticmethod
    def _build_metrics(samples: list[ControlLoopSample], tail_start: Optional[float] = None) -> CycleMetrics:

        def tail_values(value_getter: ControlSampleValueGetter) -> list[float]:
            values: list[float] = []
            for sample in samples:
                if tail_start is not None and sample.timestamp < tail_start:
                    continue

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

        def get_delta_flow_minus_setpoint(sample: ControlLoopSample) -> Optional[float]:
            return sample.state.flow_setpoint_error

        def get_delta_flow_minus_return(sample: ControlLoopSample) -> Optional[float]:
            return sample.state.flow_return_delta

        def get_relative_modulation_level(sample: ControlLoopSample) -> Optional[float]:
            return sample.state.relative_modulation_level

        return CycleMetrics(
            setpoint=build_percentiles(get_state_setpoint),
            intent_setpoint=build_percentiles(get_intent_setpoint),
            flow_temperature=build_percentiles(get_flow_temperature),
            return_temperature=build_percentiles(get_return_temperature),
            relative_modulation_level=build_percentiles(get_relative_modulation_level),
            flow_setpoint_error=build_percentiles(get_delta_flow_minus_setpoint),
            flow_return_delta=build_percentiles(get_delta_flow_minus_return),
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
    def _classify_cycle(duration: float, pwm: PWMState, tail_metrics: CycleMetrics) -> CycleClassification:
        if duration <= 0.0:
            return CycleClassification.INSUFFICIENT_DATA

        def compute_short_threshold_seconds() -> float:
            if pwm.status == PWMStatus.IDLE or pwm.duty_cycle is None:
                return TARGET_MIN_ON_TIME_SECONDS

            if (on_time_seconds := pwm.duty_cycle[0]) is None:
                return TARGET_MIN_ON_TIME_SECONDS

            return float(min(on_time_seconds * 0.9, TARGET_MIN_ON_TIME_SECONDS))

        is_short = duration < compute_short_threshold_seconds()
        is_ultra_short = duration < ULTRA_SHORT_MIN_ON_TIME_SECONDS

        if tail_metrics.flow_setpoint_error.p90 is None:
            return CycleClassification.UNCERTAIN

        overshoot = tail_metrics.flow_setpoint_error.p90 >= OVERSHOOT_MARGIN_CELSIUS
        underheat = tail_metrics.flow_setpoint_error.p90 <= -UNDERSHOOT_MARGIN_CELSIUS

        if underheat and pwm.ended_on_phase:
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

        return CycleClassification.GOOD
