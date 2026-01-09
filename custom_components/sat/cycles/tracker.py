from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Callable, Deque, Optional, TypeAlias

from homeassistant.core import HomeAssistant

from .classifier import CycleClassifier
from .const import IN_BAND_MARGIN_CELSIUS, OVERSHOOT_MARGIN_CELSIUS, OVERSHOOT_SUSTAIN_SECONDS
from .history import CycleHistory
from .types import Cycle, CycleMetrics, CycleShapeMetrics
from ..const import EVENT_SAT_CYCLE_ENDED, EVENT_SAT_CYCLE_STARTED
from ..helpers import min_max, percentile_interpolated
from ..types import CycleKind, Percentiles

if TYPE_CHECKING:
    from ..boiler import BoilerState
    from ..const import CycleClassification
    from ..coordinator import ControlLoopSample
    from ..pwm import PWMState

_LOGGER = logging.getLogger(__name__)

ControlSampleValueGetter: TypeAlias = Callable[["ControlLoopSample"], Optional[float]]


class CycleTracker:
    """Track ongoing cycles and emit completed Cycle snapshots."""

    def __init__(self, hass: HomeAssistant, history: CycleHistory, minimum_samples_per_cycle: int = 3) -> None:
        if minimum_samples_per_cycle < 1:
            raise ValueError("minimum_samples_per_cycle must be >= 1")

        self._hass = hass
        self._history = history
        self._current_samples: Deque["ControlLoopSample"] = deque()
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

    def update(self, sample: "ControlLoopSample") -> None:
        """Consume a new control-loop sample to update cycle tracking."""
        previously_active = self._last_flame_active
        currently_active = sample.state.flame_active

        if currently_active and not previously_active:
            self._history.record_off_with_demand_duration(self._compute_off_with_demand_duration(
                state=sample.state,
                timestamp=sample.timestamp,
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

    def _build_cycle_state(self, boiler_state: "BoilerState", pwm_state: "PWMState", end_time: float) -> Optional[Cycle]:
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
        kind = CycleTracker._determine_cycle_kind(fraction_domestic_hot_water, fraction_space_heating)

        flow_temperatures = [sample.state.flow_temperature for sample in samples]

        _, max_flow_temperature = min_max(flow_temperatures)

        duration = max(0.0, end_time - start_time)
        effective_warmup = min(120.0, duration * 0.25)
        observation_start = start_time + effective_warmup
        tail_start = max(observation_start, end_time - 180.0)

        base_metrics = CycleTracker._build_metrics(samples=samples)
        tail_metrics = CycleTracker._build_metrics(samples=samples, tail_start=tail_start)
        shape_metrics = CycleTracker._build_cycle_shape_metrics(
            observation_start=observation_start,
            start_time=start_time,
            samples=samples,
        )
        classification = CycleClassifier.classify(
            boiler_state=boiler_state,
            duration_seconds=duration_seconds,
            kind=kind,
            pwm_state=pwm_state,
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

    def _compute_off_with_demand_duration(self, state: "BoilerState", timestamp: float) -> Optional[float]:
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

    @staticmethod
    def _build_cycle_shape_metrics(observation_start: float, start_time: float, samples: list["ControlLoopSample"]) -> CycleShapeMetrics:
        """Compute shape metrics using flow_setpoint_error over time."""
        relevant_samples = [sample for sample in samples if sample.timestamp >= observation_start]
        if len(relevant_samples) < 2:
            return CycleShapeMetrics(
                max_flow_setpoint_error=None,
                time_in_band_seconds=0.0,
                time_to_first_overshoot_seconds=None,
                time_to_sustained_overshoot_seconds=None,
                total_overshoot_seconds=0.0,
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
            max_flow_setpoint_error=max_flow_setpoint_error,
            time_in_band_seconds=time_in_band_seconds,
            time_to_first_overshoot_seconds=time_to_first_overshoot_seconds,
            time_to_sustained_overshoot_seconds=time_to_sustained_overshoot_seconds,
            total_overshoot_seconds=total_overshoot_seconds,
        )

    @staticmethod
    def _build_metrics(samples: list["ControlLoopSample"], tail_start: Optional[float] = None) -> CycleMetrics:
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

        def get_flow_return_delta(sample: "ControlLoopSample") -> Optional[float]:
            return sample.state.flow_return_delta

        def get_flow_setpoint_error(sample: "ControlLoopSample") -> Optional[float]:
            return sample.state.flow_setpoint_error

        def get_flow_temperature(sample: "ControlLoopSample") -> Optional[float]:
            return sample.state.flow_temperature

        def get_intent_setpoint(sample: "ControlLoopSample") -> Optional[float]:
            return sample.intent.setpoint

        def get_relative_modulation_level(sample: "ControlLoopSample") -> Optional[float]:
            return sample.state.relative_modulation_level

        def get_return_temperature(sample: "ControlLoopSample") -> Optional[float]:
            return sample.state.return_temperature

        def get_state_setpoint(sample: "ControlLoopSample") -> Optional[float]:
            return sample.state.setpoint

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
    def _determine_cycle_kind(fraction_domestic_hot_water: float, fraction_space_heating: float) -> CycleKind:
        if fraction_domestic_hot_water > 0.8 and fraction_space_heating < 0.2:
            return CycleKind.DOMESTIC_HOT_WATER

        if fraction_space_heating > 0.8 and fraction_domestic_hot_water < 0.2:
            return CycleKind.CENTRAL_HEATING

        if fraction_domestic_hot_water > 0.1 and fraction_space_heating > 0.1:
            return CycleKind.MIXED

        return CycleKind.UNKNOWN

    @staticmethod
    def _classify_cycle(boiler_state: "BoilerState", duration_seconds: float, kind: CycleKind, pwm_state: "PWMState", tail_metrics: CycleMetrics) -> "CycleClassification":
        return CycleClassifier.classify(
            boiler_state=boiler_state,
            duration_seconds=duration_seconds,
            kind=kind,
            pwm_state=pwm_state,
            tail_metrics=tail_metrics,
        )
