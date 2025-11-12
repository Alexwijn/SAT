from __future__ import annotations

from collections import deque
from enum import Enum
from statistics import median
from time import monotonic
from typing import Deque, Optional, Tuple, Iterable

from custom_components.sat.boiler import BoilerStatus, BoilerState
from custom_components.sat.pwm import PWMState


class FlameStatus(str, Enum):
    HEALTHY = "healthy"
    IDLE_OK = "idle_ok"
    STUCK_ON = "stuck_on"
    STUCK_OFF = "stuck_off"
    SHORT_CYCLING = "short_cycling"
    PWM_SHORT = "pwm_short"
    INSUFFICIENT_DATA = "insufficient_data"


class Flame:
    """Tracks boiler flame on/off, maintains rolling statistics, and classifies health."""

    # Thresholds
    STUCK_OFF_SECONDS: float = 300.0
    MIN_ON_PWM_SECONDS: float = 180.0
    MIN_ON_NON_PWM_SECONDS: float = 300.0
    MAX_CYCLES_PER_HOUR_PWM: float = 8.0
    MAX_CYCLES_PER_HOUR_NON_PWM: float = 3.0
    STUCK_ON_WITHOUT_DEMAND_SECONDS: float = 120.0
    EXPECTED_MODULATING_DUTY_RATIO_WHEN_HEATING: float = 0.60

    # Windows
    DUTY_WINDOW_SECONDS: int = 15 * 60
    CYCLES_WINDOW_SECONDS: int = 60 * 60
    MEDIAN_WINDOW_SECONDS: int = 4 * 60 * 60

    # Debounce
    MEDIAN_TOLERANCE: float = 0.9
    MIN_ON_SAMPLES_FOR_HEALTH: int = 5
    MAX_DOMESTIC_HOT_WATER_IDLE_OFF_SECONDS: float = 30.0

    # Status buckets
    _TRANSIENT_STATUSES: Iterable[BoilerStatus] = (
        BoilerStatus.PREHEATING,
        BoilerStatus.PUMP_STARTING,
        BoilerStatus.WAITING_FOR_FLAME,
        BoilerStatus.INITIALIZING,
        BoilerStatus.UNKNOWN,
    )

    _HEATING_STATUSES: Iterable[BoilerStatus] = (
        BoilerStatus.HEATING_UP,
        BoilerStatus.NEAR_SETPOINT,
        BoilerStatus.AT_SETPOINT,
        BoilerStatus.COOLING_DOWN,
        BoilerStatus.OVERSHOOT_HANDLING,
        BoilerStatus.OVERSHOOT_STABILIZED,
    )

    def __init__(self, smoothing_alpha: float = 0.2) -> None:
        if not (0.0 <= smoothing_alpha <= 1.0):
            raise ValueError("smoothing_alpha must be within [0.0, 1.0].")

        # Smoothing and timing
        self._latest_on_time_seconds: float = 0.0
        self._smoothing_alpha: float = smoothing_alpha
        self._average_on_time_seconds: Optional[float] = None

        # Flame timing anchors
        self._has_completed_first_cycle: bool = False
        self._flame_on_monotonic: Optional[float] = None
        self._flame_off_monotonic: Optional[float] = None
        self._last_cycle_duration_seconds: Optional[float] = None

        # Stored states
        self._last_boiler_state: Optional[BoilerState] = None
        self._pulse_width_modulation_state: PWMState = PWMState.IDLE

        # Rolling windows
        self._last_update_monotonic: Optional[float] = None
        self._cycle_end_times_window: Deque[float] = deque()
        self._on_deltas_window: Deque[Tuple[float, float]] = deque()
        self._on_durations_window: Deque[Tuple[float, float]] = deque()

        # Health
        self._health_status: str = FlameStatus.INSUFFICIENT_DATA

    @property
    def status(self) -> str:
        return self._health_status

    @property
    def pulse_width_modulation_state(self) -> PWMState:
        return self._pulse_width_modulation_state

    @property
    def last_cycle_duration_seconds(self) -> Optional[float]:
        return self._last_cycle_duration_seconds

    @property
    def on_since(self) -> Optional[float]:
        return self._flame_on_monotonic

    @property
    def off_since(self) -> Optional[float]:
        return self._flame_off_monotonic

    @property
    def latest_on_time_seconds(self) -> Optional[float]:
        return self._latest_on_time_seconds if self._is_flame_active_internal() and self._flame_on_monotonic else None

    @property
    def average_on_time_seconds(self) -> Optional[float]:
        return self._average_on_time_seconds if self._has_completed_first_cycle else None

    def update(self, boiler_state: BoilerState, pwm_state: Optional[PWMState] = None) -> None:

        now = monotonic()

        if self._last_update_monotonic is None:
            self._last_update_monotonic = now

        if pwm_state is not None:
            self._pulse_width_modulation_state = pwm_state

        self._last_boiler_state = boiler_state

        # Accumulate duty window with elapsed time since last tick
        elapsed = max(0.0, now - self._last_update_monotonic)

        previously_active = self._is_flame_active_internal()
        currently_active = bool(boiler_state.flame_active)

        if previously_active and elapsed > 0.0:
            self._on_deltas_window.append((now, elapsed))

        self._prune_duty_window(now)

        # OFF -> ON
        if currently_active and not previously_active:
            self._flame_on_monotonic = now
            self._flame_off_monotonic = None
            self._last_update_monotonic = now
            self._recompute_health(now)
            return

        # ON -> ON
        if currently_active and self._flame_on_monotonic is not None:
            self._latest_on_time_seconds = (
                float(boiler_state.flame_average_on_time_seconds) if boiler_state.flame_average_on_time_seconds is not None else (now - self._flame_on_monotonic)
            )

            if self._has_completed_first_cycle:
                self._average_on_time_seconds = (
                    self._latest_on_time_seconds
                    if self._average_on_time_seconds is None
                    else (1.0 - self._smoothing_alpha) * self._average_on_time_seconds + self._smoothing_alpha * self._latest_on_time_seconds
                )

            self._last_update_monotonic = now
            self._recompute_health(now)
            return

        # ON -> OFF
        if not currently_active and previously_active:
            duration = (
                float(boiler_state.flame_average_on_time_seconds)
                if boiler_state.flame_average_on_time_seconds is not None
                else ((now - self._flame_on_monotonic) if self._flame_on_monotonic is not None else 0.0)
            )

            self._last_cycle_duration_seconds = duration
            self._flame_on_monotonic = None
            self._flame_off_monotonic = now

            self._cycle_end_times_window.append(now)
            self._on_durations_window.append((now, duration))
            self._prune_cycles_window(now)
            self._prune_median_window(now)

            self._has_completed_first_cycle = True
            self._last_update_monotonic = now
            self._recompute_health(now)
            return

        # OFF -> OFF
        if not currently_active:
            if self._flame_off_monotonic is None:
                self._flame_off_monotonic = now

            self._last_update_monotonic = now
            self._recompute_health(now)
            return

    def _recompute_health(self, now: float) -> None:

        state = self._last_boiler_state
        if state is None:
            self._health_status = FlameStatus.INSUFFICIENT_DATA
            return

        last_on_seconds = self._latest_on_time_seconds if (state.flame_active and self._flame_on_monotonic) else 0.0
        last_off_seconds = (now - self._flame_off_monotonic) if (not state.flame_active and self._flame_off_monotonic) else 0.0

        cycles_per_hour = self._cycles_per_hour(now)
        duty_ratio = self._duty_ratio_last_window(now)
        median_on_seconds, sample_count = self._median_on_duration(now)

        domestic_hot_water_active = bool(state.hot_water_active)

        # Heating demand is derived from device_active or heating-related statuses
        heating_demand = bool(state.device_active) or (state.device_status in self._HEATING_STATUSES)

        # Modulation capability inferred from the presence of a numeric relative_modulation_level
        modulating_boiler = (state.relative_modulation_level is not None and isinstance(state.relative_modulation_level, (int, float)))

        # Thin history: still allow stuck checks, avoid cycling judgments
        if sample_count < self.MIN_ON_SAMPLES_FOR_HEALTH:
            if not heating_demand and not domestic_hot_water_active:
                self._health_status = (FlameStatus.STUCK_ON if (state.flame_active and last_on_seconds > self.STUCK_ON_WITHOUT_DEMAND_SECONDS) else FlameStatus.IDLE_OK)
                return

            timeout = self.MAX_DOMESTIC_HOT_WATER_IDLE_OFF_SECONDS if domestic_hot_water_active else self.STUCK_OFF_SECONDS
            if (not state.flame_active) and last_off_seconds > timeout and state.device_status not in self._TRANSIENT_STATUSES:
                self._health_status = FlameStatus.STUCK_OFF
            else:
                self._health_status = FlameStatus.INSUFFICIENT_DATA

            return

        # No demand and not domestic hot water
        if not heating_demand and not domestic_hot_water_active:
            if state.flame_active and last_on_seconds > self.STUCK_ON_WITHOUT_DEMAND_SECONDS:
                self._health_status = FlameStatus.STUCK_ON
            else:
                self._health_status = FlameStatus.IDLE_OK

            return

        # Domestic hot water: allow continuous flame; only stuck-off if off too long (and not transient)
        if domestic_hot_water_active:
            if (not state.flame_active) and last_off_seconds > self.MAX_DOMESTIC_HOT_WATER_IDLE_OFF_SECONDS and state.device_status not in self._TRANSIENT_STATUSES:
                self._health_status = FlameStatus.STUCK_OFF
            else:
                self._health_status = FlameStatus.HEALTHY

            return

        # Space heating demand
        if (not state.flame_active) and last_off_seconds > self.STUCK_OFF_SECONDS and state.device_status not in self._TRANSIENT_STATUSES:
            self._health_status = FlameStatus.STUCK_OFF
            return

        if self._pulse_width_modulation_state == PWMState.ON:
            if cycles_per_hour > self.MAX_CYCLES_PER_HOUR_PWM:
                self._health_status = FlameStatus.SHORT_CYCLING
                return

            if median_on_seconds is not None and median_on_seconds < self.MEDIAN_TOLERANCE * self.MIN_ON_PWM_SECONDS:
                self._health_status = FlameStatus.PWM_SHORT
                return

            self._health_status = FlameStatus.HEALTHY
            return

        # Non-PWM: internal regulation; do not require continuous flame
        if modulating_boiler:
            if cycles_per_hour <= 2.0 and duty_ratio >= 0.8 * self.EXPECTED_MODULATING_DUTY_RATIO_WHEN_HEATING:
                self._health_status = FlameStatus.HEALTHY
                return

            if cycles_per_hour > self.MAX_CYCLES_PER_HOUR_NON_PWM and median_on_seconds is not None and median_on_seconds < self.MEDIAN_TOLERANCE * self.MIN_ON_NON_PWM_SECONDS:
                self._health_status = FlameStatus.SHORT_CYCLING
                return

            self._health_status = FlameStatus.HEALTHY
            return

        # Non-modulating
        if cycles_per_hour > self.MAX_CYCLES_PER_HOUR_NON_PWM and median_on_seconds is not None and median_on_seconds < self.MEDIAN_TOLERANCE * self.MIN_ON_NON_PWM_SECONDS:
            self._health_status = FlameStatus.SHORT_CYCLING
            return

        self._health_status = FlameStatus.HEALTHY

    def _prune_duty_window(self, now: float) -> None:
        cutoff = now - self.DUTY_WINDOW_SECONDS
        while self._on_deltas_window and self._on_deltas_window[0][0] < cutoff:
            self._on_deltas_window.popleft()

    def _prune_cycles_window(self, now: float) -> None:
        cutoff = now - self.CYCLES_WINDOW_SECONDS
        while self._cycle_end_times_window and self._cycle_end_times_window[0] < cutoff:
            self._cycle_end_times_window.popleft()

    def _prune_median_window(self, now: float) -> None:
        cutoff = now - self.MEDIAN_WINDOW_SECONDS
        while self._on_durations_window and self._on_durations_window[0][0] < cutoff:
            self._on_durations_window.popleft()

    def _duty_ratio_last_window(self, now: float) -> float:
        self._prune_duty_window(now)
        on_seconds = sum(delta for _, delta in self._on_deltas_window)
        return min(1.0, max(0.0, on_seconds / float(self.DUTY_WINDOW_SECONDS)))

    def _cycles_per_hour(self, now: float) -> float:
        self._prune_cycles_window(now)
        return float(len(self._cycle_end_times_window))

    def _median_on_duration(self, now: float) -> Tuple[Optional[float], int]:
        self._prune_median_window(now)
        if not self._on_durations_window:
            return None, 0

        durations = [duration for _, duration in self._on_durations_window]
        return float(median(durations)), len(durations)

    def _is_flame_active_internal(self) -> bool:
        return bool(self._last_boiler_state.flame_active) if self._last_boiler_state else False
