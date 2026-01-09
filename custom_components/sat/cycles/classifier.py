from __future__ import annotations

from typing import TYPE_CHECKING

from .const import *
from ..const import COLD_SETPOINT, CycleClassification
from ..types import CycleKind, PWMStatus

if TYPE_CHECKING:
    from ..boiler import BoilerState
    from ..pwm import PWMState
    from .types import CycleMetrics


class CycleClassifier:
    @staticmethod
    def classify(boiler_state: "BoilerState", duration_seconds: float, kind: CycleKind, pwm_state: "PWMState", tail_metrics: "CycleMetrics") -> CycleClassification:
        """Classify a cycle based on duration, PWM state, and tail error metrics."""
        if duration_seconds <= 0.0:
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

        is_short = duration_seconds < compute_short_threshold_seconds()
        is_ultra_short = duration_seconds < ULTRA_SHORT_MIN_ON_TIME_SECONDS

        if tail_metrics.flow_setpoint_error.p90 is None:
            return CycleClassification.UNCERTAIN

        overshoot = tail_metrics.flow_setpoint_error.p90 >= OVERSHOOT_MARGIN_CELSIUS
        underheat = tail_metrics.flow_setpoint_error.p90 <= -UNDERSHOOT_MARGIN_CELSIUS

        if is_ultra_short:
            if overshoot:
                return CycleClassification.FAST_OVERSHOOT

            if underheat:
                return CycleClassification.FAST_UNDERHEAT

        if is_short:
            if overshoot:
                return CycleClassification.TOO_SHORT_OVERSHOOT

            if underheat:
                if boiler_state.setpoint < COLD_SETPOINT:
                    return CycleClassification.UNCERTAIN

                return CycleClassification.TOO_SHORT_UNDERHEAT

        if underheat:
            return CycleClassification.LONG_UNDERHEAT

        if overshoot:
            return CycleClassification.LONG_OVERSHOOT

        if pwm_state.status == PWMStatus.ON:
            return CycleClassification.PREMATURE_OFF

        return CycleClassification.GOOD
