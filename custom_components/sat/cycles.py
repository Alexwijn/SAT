from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from statistics import median
from time import monotonic
from typing import Deque, List, Optional, Tuple, TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .const import CycleKind, CycleClassification, EVENT_SAT_CYCLE_STARTED, EVENT_SAT_CYCLE_ENDED
from .helpers import clamp
from .pwm import PWMState

if TYPE_CHECKING:
    from .boiler import BoilerState

_LOGGER = logging.getLogger(__name__)

# What we consider a "good" minimum burn length
TARGET_MIN_ON_TIME_SECONDS: float = 300.0  # 5 minutes

# Low-load detection thresholds
LOW_LOAD_MIN_CYCLES_PER_HOUR: float = 3.0
LOW_LOAD_MAX_DUTY_RATIO_15_M: float = 0.50

# Flow vs. setpoint classification margins (remember: many boilers report integer flow temperatures)
OVERSHOOT_MARGIN_CELSIUS: float = 2.0  # max_flow >= setpoint + margin -> overshoot
UNDERSHOOT_MARGIN_CELSIUS: float = 2.0  # max_flow <= setpoint - margin -> underheat


@dataclass(frozen=True, slots=True)
class CycleSample:
    timestamp: float
    boiler_state: BoilerState


@dataclass(frozen=True, slots=True)
class Cycle:
    kind: CycleKind
    classification: CycleClassification

    end: float
    start: float
    sample_count: int
    duration: float

    # Averages over the samples where the value was not None
    average_setpoint: Optional[float]
    average_flow_temperature: Optional[float]
    average_return_temperature: Optional[float]
    average_relative_modulation_level: Optional[float]

    # Extremes (min / max) over the samples where the value was not None
    min_flow_temperature: Optional[float]
    max_flow_temperature: Optional[float]
    min_return_temperature: Optional[float]
    max_return_temperature: Optional[float]

    # Fractions of time in DHW vs. space heating (approximated by sample counts)
    fraction_domestic_hot_water: float
    fraction_space_heating: float


@dataclass(frozen=True, slots=True)
class CycleStatistics:
    sample_count_4h: int
    last_hour_count: float
    duty_ratio_last_15m: float
    off_with_demand_duration: Optional[float]
    median_on_duration_seconds_4h: Optional[float]


class CycleHistory:
    """ Rolling history of flame cycles for statistical analysis."""

    def __init__(self, duty_window_seconds: int = 15 * 60, cycles_window_seconds: int = 60 * 60, median_window_seconds: int = 4 * 60 * 60) -> None:

        if duty_window_seconds <= 0:
            raise ValueError("duty_window_seconds must be > 0")

        if cycles_window_seconds <= 0:
            raise ValueError("cycles_window_seconds must be > 0")

        if median_window_seconds <= 0:
            raise ValueError("median_window_seconds must be > 0")

        self._duty_window_seconds: int = duty_window_seconds
        self._cycles_window_seconds: int = cycles_window_seconds
        self._median_window_seconds: int = median_window_seconds

        # (cycle_end_time, duration_seconds)
        self._cycle_end_times_window: Deque[Tuple[float, float]] = deque()

        # For median on-duration (timestamp, duration_seconds)
        self._on_durations_window: Deque[Tuple[float, float]] = deque()

        # Last completed cycle
        self._last_cycle: Optional[Cycle] = None

        # Last OFF-with-demand duration (seconds) measured just before the most recent cycle started
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
        """Cycles per hour, extrapolated from the cycle's window."""
        now = self._current_time_hint()
        if now is not None:
            self._prune_cycles_window(now)

        count = len(self._cycle_end_times_window)
        return count * 3600.0 / self._cycles_window_seconds

    @property
    def duty_ratio_last_15m(self) -> float:
        """Duty ratio (0.0–1.0) over the duty window, derived from recorded cycles."""
        now = self._current_time_hint()
        if now is None:
            return 0.0

        cutoff = now - self._duty_window_seconds
        on_seconds = sum(duration for end_time, duration in self._cycle_end_times_window if end_time >= cutoff)

        if on_seconds <= 0.0:
            return 0.0

        ratio = on_seconds / float(self._duty_window_seconds)
        return clamp(ratio, 0.0, 1.0)

    @property
    def median_on_duration_seconds_4h(self) -> Optional[float]:
        """
        Median ON duration of completed cycles in the median window.
        """
        now = self._current_time_hint()
        if now is not None:
            self._prune_median_window(now)

        if not self._on_durations_window:
            return None

        durations = [duration for _, duration in self._on_durations_window]
        return float(median(durations))

    @property
    def last_cycle(self) -> Optional[Cycle]:
        return self._last_cycle

    @property
    def statistics(self) -> CycleStatistics:
        """Return a snapshot of rolling metrics."""
        return CycleStatistics(
            sample_count_4h=self.sample_count_4h,
            last_hour_count=self.cycles_last_hour,
            duty_ratio_last_15m=self.duty_ratio_last_15m,
            off_with_demand_duration=self._off_with_demand_duration,
            median_on_duration_seconds_4h=self.median_on_duration_seconds_4h,
        )

    def record_cycle(self, cycle: Cycle) -> None:
        """Record a completed flame cycle."""
        end_time = cycle.end
        duration = max(0.0, cycle.duration)

        self._on_durations_window.append((end_time, duration))
        self._cycle_end_times_window.append((end_time, duration))

        self._prune_cycles_window(end_time)
        self._prune_median_window(end_time)

        self._last_cycle = cycle

        _LOGGER.debug(
            "Recorded cycle kind=%s, classification=%s duration=%.1fs, cycles_last_hour=%.1f, samples_4h=%d",
            cycle.kind.name, cycle.classification.name, duration, self.cycles_last_hour, self.sample_count_4h
        )

    def record_off_with_demand_duration(self, duration: Optional[float]) -> None:
        """
        Record the OFF-with-demand duration (seconds) immediately before the most recent cycle started.
        """
        self._off_with_demand_duration = duration

    def _current_time_hint(self) -> Optional[float]:
        candidates: List[float] = []
        if self._cycle_end_times_window:
            candidates.append(self._cycle_end_times_window[-1][0])

        if self._on_durations_window:
            candidates.append(self._on_durations_window[-1][0])

        if not candidates:
            return None

        return max(candidates)

    def _prune_cycles_window(self, now: float) -> None:
        cutoff = now - self._cycles_window_seconds
        while self._cycle_end_times_window and self._cycle_end_times_window[0][0] < cutoff:
            self._cycle_end_times_window.popleft()

    def _prune_median_window(self, now: float) -> None:
        cutoff = now - self._median_window_seconds
        while self._on_durations_window and self._on_durations_window[0][0] < cutoff:
            self._on_durations_window.popleft()


class CycleTracker:
    """Detects and summarizes flame cycles using BoilerState.flame_active."""

    def __init__(self, hass: HomeAssistant, history: CycleHistory, minimum_samples_per_cycle: int = 3) -> None:
        if minimum_samples_per_cycle < 1:
            raise ValueError("minimum_samples_per_cycle must be >= 1")

        self._hass = hass
        self._history: CycleHistory = history
        self._current_samples: Deque[CycleSample] = deque()
        self._minimum_samples_per_cycle: int = minimum_samples_per_cycle

        self._last_flame_active: Optional[bool] = None
        self._last_pwm_state: Optional[PWMState] = None
        self._current_cycle_start: Optional[float] = None
        self._last_flame_off_timestamp: Optional[float] = None

    @property
    def started_since(self) -> Optional[float]:
        return self._current_cycle_start

    def reset(self) -> None:
        """Reset the internal tracking state (does not clear history)."""
        self._current_samples.clear()
        self._last_flame_active = None
        self._current_cycle_start = None
        self._last_flame_off_timestamp = None

    def update(self, boiler_state: BoilerState, pwm_state: Optional[PWMState] = None, timestamp: float = None) -> None:
        timestamp = timestamp or monotonic()
        previously_active = self._last_flame_active
        currently_active = boiler_state.flame_active

        if pwm_state is not None:
            self._last_pwm_state = pwm_state

        # OFF -> ON: start a new cycle
        if currently_active and not previously_active:
            # Compute OFF-with-demand duration since last flame OFF, if any.
            off_with_demand_duration: Optional[float] = None

            if self._last_flame_off_timestamp is not None:
                off_duration = max(0.0, timestamp - self._last_flame_off_timestamp)

                demand_present = (
                        not boiler_state.hot_water_active
                        and boiler_state.is_active
                        and boiler_state.setpoint is not None
                        and boiler_state.flow_temperature is not None
                        and boiler_state.setpoint > boiler_state.flow_temperature
                )

                if demand_present:
                    off_with_demand_duration = off_duration
                else:
                    self._last_flame_off_timestamp = None

            # Store OFF-with-demand duration
            self._history.record_off_with_demand_duration(off_with_demand_duration)

            _LOGGER.debug("Flame transition OFF->ON, starting new cycle.")

            # Notify listeners that a new cycle has started
            self._hass.bus.fire(EVENT_SAT_CYCLE_STARTED)

            self._current_cycle_start = timestamp
            self._current_samples.clear()

        # ON -> ON: accumulate samples
        if currently_active:
            self._current_samples.append(CycleSample(timestamp=timestamp, boiler_state=boiler_state))

        # ON -> OFF: finalize cycle
        if not currently_active and previously_active:
            _LOGGER.debug("Flame transition ON->OFF, finalizing cycle.")

            # Remember when we turned off to compute OFF-with-demand before the next cycle
            self._last_flame_off_timestamp = timestamp

            cycle_state = self._build_cycle_state(timestamp)
            self._hass.bus.fire(EVENT_SAT_CYCLE_ENDED, {"cycle": cycle_state})

            # Push into history
            if cycle_state is not None:
                self._history.record_cycle(cycle_state)

            # Reset for the next potential cycle
            self._current_cycle_start = None
            self._current_samples.clear()

        self._last_flame_active = currently_active

    def _build_cycle_state(self, end_time: float) -> Optional[Cycle]:
        if self._current_cycle_start is None:
            _LOGGER.debug("No start time, ignoring cycle.")
            return None

        sample_count = len(self._current_samples)
        duration = max(0.0, end_time - self._current_cycle_start)

        if sample_count < self._minimum_samples_per_cycle:
            _LOGGER.debug("Too few samples (%d < %d), ignoring cycle.", sample_count, self._minimum_samples_per_cycle)
            return None

        samples: List[CycleSample] = list(self._current_samples)

        # Determine cycle kind and fractions
        dhw_count = sum(1 for s in samples if s.boiler_state.hot_water_active)
        heating_count = sum(1 for sample in samples if not sample.boiler_state.hot_water_active and sample.boiler_state.is_active)

        fraction_dhw = dhw_count / float(sample_count) if sample_count else 0.0
        fraction_heating = heating_count / float(sample_count) if sample_count else 0.0

        if fraction_dhw > 0.8 and fraction_heating < 0.2:
            kind = CycleKind.DOMESTIC_HOT_WATER
        elif fraction_heating > 0.8 and fraction_dhw < 0.2:
            kind = CycleKind.CENTRAL_HEATING
        elif fraction_dhw > 0.1 and fraction_heating > 0.1:
            kind = CycleKind.MIXED
        else:
            kind = CycleKind.UNKNOWN

        # Aggregate numeric fields
        def _avg(values: List[Optional[float]]) -> Optional[float]:
            filtered = [value for value in values if value is not None]
            if not filtered:
                return None

            return sum(filtered) / float(len(filtered))

        setpoints = [sample.boiler_state.setpoint for sample in samples]
        flow_temperatures = [sample.boiler_state.flow_temperature for sample in samples]
        return_temperatures = [sample.boiler_state.return_temperature for sample in samples]
        modulation_levels = [sample.boiler_state.relative_modulation_level for sample in samples]

        average_setpoint = _avg(setpoints)
        average_flow_temperature = _avg(flow_temperatures)
        average_return_temperature = _avg(return_temperatures)
        average_relative_modulation_level = _avg(modulation_levels)

        min_flow_temperature = min((value for value in flow_temperatures if value is not None), default=None)
        max_flow_temperature = max((value for value in flow_temperatures if value is not None), default=None)
        min_return_temperature = min((value for value in return_temperatures if value is not None), default=None)
        max_return_temperature = max((value for value in return_temperatures if value is not None), default=None)

        return Cycle(
            kind=kind,

            classification=self._classify_cycle(
                duration=duration,
                pwm_state=self._last_pwm_state,
                average_setpoint=average_setpoint,
                statistics=self._history.statistics,
                max_flow_temperature=max_flow_temperature,
            ),

            end=end_time,
            duration=duration,
            sample_count=sample_count,
            start=self._current_cycle_start,

            average_setpoint=average_setpoint,
            average_flow_temperature=average_flow_temperature,
            average_return_temperature=average_return_temperature,
            average_relative_modulation_level=average_relative_modulation_level,

            min_flow_temperature=min_flow_temperature,
            max_flow_temperature=max_flow_temperature,
            min_return_temperature=min_return_temperature,
            max_return_temperature=max_return_temperature,

            fraction_domestic_hot_water=fraction_dhw,
            fraction_space_heating=fraction_heating,
        )

    @staticmethod
    def _classify_cycle(duration: float, statistics: CycleStatistics, pwm_state: Optional[PWMState], max_flow_temperature: Optional[float], average_setpoint: Optional[float]) -> CycleClassification:
        """Decide what the last cycle implies for minimum-setpoint tuning."""
        if duration <= 0.0 or average_setpoint is None:
            return CycleClassification.INSUFFICIENT_DATA

        if max_flow_temperature is None:
            # Without temperature, we cannot tell underheating vs. overshooting.
            if duration < TARGET_MIN_ON_TIME_SECONDS:
                return CycleClassification.UNCERTAIN

            return CycleClassification.GOOD

        overshoot_margin = OVERSHOOT_MARGIN_CELSIUS
        undershoot_margin = UNDERSHOOT_MARGIN_CELSIUS

        overshoot = max_flow_temperature >= average_setpoint + overshoot_margin
        underheat = max_flow_temperature <= average_setpoint - undershoot_margin

        short_threshold = TARGET_MIN_ON_TIME_SECONDS
        if pwm_state is not None and pwm_state.enabled and pwm_state.duty_cycle:
            short_threshold = pwm_state.duty_cycle[0]

        # Short burns
        if duration < short_threshold:
            if underheat and not overshoot:
                return CycleClassification.TOO_SHORT_UNDERHEAT

            if overshoot and not underheat:
                return CycleClassification.TOO_SHORT_OVERSHOOT

            return CycleClassification.UNCERTAIN

        # Longer burns: check for short-cycling with overshoot
        short_cycling_context = (
                statistics.last_hour_count > LOW_LOAD_MIN_CYCLES_PER_HOUR * 2.0
                and statistics.duty_ratio_last_15m < LOW_LOAD_MAX_DUTY_RATIO_15_M
        )

        # Clear low-load overshoot behavior: many cycles/hour, low duty, flow above setpoint
        if short_cycling_context and overshoot and not underheat:
            return CycleClassification.SHORT_CYCLING_OVERSHOOT

        # All other long burns are "good enough" for minimum tuning.
        return CycleClassification.GOOD
