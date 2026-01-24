from __future__ import annotations

from typing import TYPE_CHECKING

from .const import *
from ..const import COLD_SETPOINT
from ..types import CycleClassification, CycleKind, PWMStatus

if TYPE_CHECKING:
    from ..pwm import PWMState
    from ..device import DeviceState
    from .types import CycleMetrics


class CycleClassifier:
    @staticmethod
    def classify(device_state: "DeviceState", duration_seconds: float, kind: CycleKind, pwm_state: "PWMState", tail_metrics: "CycleMetrics") -> CycleClassification:
        """Classify a cycle based on its kind, setpoint, duration and some other metrics."""
        if duration_seconds <= 0.0:
            return CycleClassification.INSUFFICIENT_DATA

        if kind == CycleKind.UNKNOWN:
            return CycleClassification.UNCERTAIN

        if tail_metrics.hot_water_active_fraction > 0.0:
            return CycleClassification.UNCERTAIN

        if pwm_state.enabled and pwm_state.status == PWMStatus.IDLE:
            return CycleClassification.UNCERTAIN

        if pwm_state.enabled and pwm_state.status != PWMStatus.IDLE:
            return CycleClassifier._classify_pwm(duration_seconds, pwm_state, tail_metrics, device_state)

        if (classification := CycleClassifier._classify_continuous(tail_metrics, device_state)) is not None:
            return classification

        return CycleClassification.GOOD

    @staticmethod
    def _classify_pwm(duration_seconds: float, pwm_state: "PWMState", tail_metrics: "CycleMetrics", device_state: "DeviceState") -> CycleClassification:
        """Classify a cycle based on its PWM state and some other metrics."""
        if tail_metrics.flow_requested_setpoint_error.p50 is None or tail_metrics.flow_requested_setpoint_error.p90 is None:
            return CycleClassification.UNCERTAIN

        underheat = tail_metrics.flow_requested_setpoint_error.p90 <= UNDERSHOOT_MARGIN_CELSIUS

        if underheat:
            if (effective_setpoint := tail_metrics.requested_setpoint.p50) is None:
                effective_setpoint = device_state.setpoint

            if effective_setpoint is not None and effective_setpoint < COLD_SETPOINT:
                return CycleClassification.UNCERTAIN

            return CycleClassification.UNDERHEAT

        if pwm_state.status == PWMStatus.ON:
            if pwm_state.on_time_seconds is None:
                return CycleClassification.SHORT_CYCLING

            if pwm_state.on_time_seconds <= 0:
                return CycleClassification.SHORT_CYCLING

            if duration_seconds < pwm_state.on_time_seconds * 0.8:
                return CycleClassification.SHORT_CYCLING

        return CycleClassification.GOOD

    @staticmethod
    def _classify_continuous(tail_metrics: "CycleMetrics", device_state: "DeviceState") -> CycleClassification | None:
        """Classify a cycle based on its flow control setpoint error and some other metrics."""
        if tail_metrics.flow_control_setpoint_error.p90 is None or tail_metrics.flow_control_setpoint_error.p50 is None:
            return CycleClassification.UNCERTAIN

        overshoot = tail_metrics.flow_control_setpoint_error.p90 >= OVERSHOOT_MARGIN_CELSIUS
        underheat = tail_metrics.flow_control_setpoint_error.p50 <= UNDERSHOOT_MARGIN_CELSIUS

        if overshoot:
            return CycleClassification.OVERSHOOT

        if underheat:
            effective_setpoint = tail_metrics.requested_setpoint.p50
            if effective_setpoint is None:
                effective_setpoint = device_state.setpoint

            if effective_setpoint is not None and effective_setpoint < COLD_SETPOINT:
                return CycleClassification.UNCERTAIN

            return CycleClassification.UNDERHEAT

        return None
