"""Tests for cycle tracking and classification."""

from typing import Optional

import pytest

from custom_components.sat.device import DeviceState
from custom_components.sat.cycles import Cycle, CycleHistory, CycleMetrics, CycleShapeMetrics, CycleTracker
from custom_components.sat.cycles.const import (
    OVERSHOOT_MARGIN_CELSIUS,
    UNDERSHOOT_MARGIN_CELSIUS,
    DAILY_WINDOW_SECONDS,
    TARGET_MIN_ON_TIME_SECONDS,
)
from custom_components.sat.const import COLD_SETPOINT
from custom_components.sat.heating_control import ControlLoopSample
from custom_components.sat.helpers import timestamp
from custom_components.sat.pwm import PWMState
from custom_components.sat.types import CycleClassification, CycleControlMode, CycleKind, Percentiles, PWMStatus


def _make_pwm_state(status: PWMStatus = PWMStatus.IDLE, *, enabled: bool = False) -> PWMState:
    return PWMState(
        enabled=enabled,
        status=status,
        duty_cycle=None,
        last_duty_cycle_percentage=None,
    )


def _make_boiler_state(
        *,
        flame_active: bool,
        setpoint: float = 40.0,
        flow_temperature: float = 40.0,
        hot_water_active: bool = False,
        central_heating: bool = True,
) -> DeviceState:
    return DeviceState(
        flame_active=flame_active,
        central_heating=central_heating,
        hot_water_active=hot_water_active,
        setpoint=setpoint,
        flow_temperature=flow_temperature,
        return_temperature=flow_temperature - 10.0,
        relative_modulation_level=None,
        max_modulation_level=100,
    )


def _make_sample(
        *,
        timestamp: float,
        flame_active: bool,
        setpoint: float = 40.0,
        flow_temperature: float = 40.0,
        hot_water_active: bool = False,
) -> ControlLoopSample:
    return ControlLoopSample(
        timestamp=timestamp,
        pwm=_make_pwm_state(PWMStatus.IDLE),
        device_state=_make_boiler_state(
            flame_active=flame_active,
            setpoint=setpoint,
            flow_temperature=flow_temperature,
            hot_water_active=hot_water_active,
        ),
        control_setpoint=setpoint,
        relative_modulation=None,
        outside_temperature=5.0,
        requested_setpoint=setpoint,
    )


def _tail_metrics_for_errors(
        control_error: Optional[float],
        requested_error: Optional[float],
        *,
        setpoint: float = 40.0,
        hot_water_fraction: float = 0.0,
) -> CycleMetrics:
    return CycleMetrics(
        requested_setpoint=Percentiles(p50=setpoint, p90=setpoint),
        control_setpoint=Percentiles(p50=setpoint, p90=setpoint),
        flow_temperature=Percentiles(p50=40.0, p90=40.0),
        return_temperature=Percentiles(p50=30.0, p90=30.0),
        relative_modulation_level=Percentiles(p50=None, p90=None),
        flow_return_delta=Percentiles(p50=10.0, p90=10.0),
        flow_control_setpoint_error=Percentiles(p50=control_error, p90=control_error),
        flow_requested_setpoint_error=Percentiles(p50=requested_error, p90=requested_error),
        hot_water_active_fraction=hot_water_fraction,
    )


def _tail_metrics_for_error(error: Optional[float], *, setpoint: float = 40.0, hot_water_fraction: float = 0.0) -> CycleMetrics:
    return _tail_metrics_for_errors(
        error,
        error,
        setpoint=setpoint,
        hot_water_fraction=hot_water_fraction,
    )


def _shape_metrics(duration: float) -> CycleShapeMetrics:
    return CycleShapeMetrics(
        total_overshoot_seconds=0,
        max_flow_control_setpoint_error=0,
        time_in_band_seconds=duration,
        time_to_first_overshoot_seconds=None,
        time_to_sustained_overshoot_seconds=duration,
    )


def _make_cycle(end_time: float, duration: float) -> Cycle:
    metrics = _tail_metrics_for_error(0.0)
    return Cycle(
        kind=CycleKind.CENTRAL_HEATING,
        control_mode=CycleControlMode.CONTINUOUS,
        tail=metrics,
        metrics=metrics,
        shape=_shape_metrics(duration),
        classification=CycleClassification.GOOD,
        start=end_time - duration,
        end=end_time,
        sample_count=3,
        min_flow_temperature=35.0,
        max_flow_temperature=45.0,
        fraction_space_heating=1.0,
        fraction_domestic_hot_water=0.0,
    )


def test_classify_short_cycling_when_pwm_on():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.ON, enabled=True)
    tail_metrics = _tail_metrics_for_error(0.0)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.SHORT_CYCLING


def test_classify_pwm_enabled_idle_uncertain():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.IDLE, enabled=True)
    tail_metrics = _tail_metrics_for_error(0.0)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=300,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.UNCERTAIN



def test_classify_overshoot():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(OVERSHOOT_MARGIN_CELSIUS + 0.1)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=300,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.OVERSHOOT


def test_classify_pwm_uses_requested_error():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.OFF, enabled=True)
    tail_metrics = _tail_metrics_for_errors(0.2, OVERSHOOT_MARGIN_CELSIUS + 0.3)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.GOOD


def test_classify_pwm_ignores_control_setpoint_overshoot():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.OFF, enabled=True)
    tail_metrics = _tail_metrics_for_errors(OVERSHOOT_MARGIN_CELSIUS + 0.3, 0.1)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.GOOD


def test_classify_pwm_underheat():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.OFF, enabled=True)
    tail_metrics = _tail_metrics_for_errors(0.1, UNDERSHOOT_MARGIN_CELSIUS - 0.2)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.UNDERHEAT


def test_classify_too_short_underheat():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(UNDERSHOOT_MARGIN_CELSIUS - 0.2)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS * 0.5,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.UNDERHEAT


def test_classify_long_underheat():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(UNDERSHOOT_MARGIN_CELSIUS - 0.3)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.UNDERHEAT


def test_classify_long_underheat_below_cold_setpoint():
    boiler_state = _make_boiler_state(flame_active=False, setpoint=COLD_SETPOINT - 5.0)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(UNDERSHOOT_MARGIN_CELSIUS - 0.3)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.UNDERHEAT


def test_classify_short_underheat_below_cold_setpoint_uncertain():
    boiler_state = _make_boiler_state(flame_active=False, setpoint=COLD_SETPOINT - 5.0)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(UNDERSHOOT_MARGIN_CELSIUS - 0.2, setpoint=COLD_SETPOINT - 5.0)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS * 0.5,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.UNCERTAIN


def test_classify_good_cycle():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(0.0)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.GOOD


def test_classify_hot_water_cycle_uncertain():
    boiler_state = _make_boiler_state(flame_active=False, hot_water_active=True)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(OVERSHOOT_MARGIN_CELSIUS + 0.4, hot_water_fraction=1.0)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.DOMESTIC_HOT_WATER,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.UNCERTAIN


def test_classify_unknown_cycle_uncertain():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(OVERSHOOT_MARGIN_CELSIUS + 0.4)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.UNKNOWN,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.UNCERTAIN


def test_classify_cycle_uncertain_when_dhw_in_tail():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(OVERSHOOT_MARGIN_CELSIUS + 0.4, hot_water_fraction=0.4)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.UNCERTAIN


def test_cycle_tracker_records_cycle(hass):
    history = CycleHistory()
    tracker = CycleTracker(hass, history, minimum_samples_per_cycle=3)
    base = timestamp()

    tracker.update(_make_sample(timestamp=base, flame_active=False))
    tracker.update(_make_sample(timestamp=base + 1.0, flame_active=True))
    tracker.update(_make_sample(timestamp=base + 2.0, flame_active=True))
    tracker.update(_make_sample(timestamp=base + 3.0, flame_active=True))
    tracker.update(_make_sample(timestamp=base + 4.0, flame_active=False))

    assert history.last_cycle is not None
    assert history.last_cycle.sample_count == 3


def test_cycle_tracker_ignores_short_cycles(hass):
    history = CycleHistory()
    tracker = CycleTracker(hass, history, minimum_samples_per_cycle=3)
    base = timestamp()

    tracker.update(_make_sample(timestamp=base, flame_active=False))
    tracker.update(_make_sample(timestamp=base + 1.0, flame_active=True))
    tracker.update(_make_sample(timestamp=base + 2.0, flame_active=True))
    tracker.update(_make_sample(timestamp=base + 3.0, flame_active=False))

    assert history.last_cycle is None


def test_cycle_history_rates():
    history = CycleHistory()
    base = timestamp()

    history.record_cycle(_make_cycle(base, 100.0))
    history.record_cycle(_make_cycle(base + 300.0, 100.0))
    history.record_cycle(_make_cycle(base + 600.0, 100.0))

    statistics = history.statistics
    assert statistics.window.recent.sample_count == 3
    assert statistics.window.daily.sample_count == 3
    assert statistics.window.recent.duty_ratio == pytest.approx(300.0 / (4 * 3600.0), abs=0.0001)
    assert statistics.window.daily.duty_ratio == pytest.approx(300.0 / DAILY_WINDOW_SECONDS, abs=0.0001)


def test_cycle_history_daily_window_prunes():
    history = CycleHistory()
    base = timestamp()

    history.record_cycle(_make_cycle(base - DAILY_WINDOW_SECONDS - 20.0, 100.0))
    history.record_cycle(_make_cycle(base - 10.0, 100.0))

    assert history.window_statistics.daily.sample_count == 1


def test_cycle_history_daily_statistics():
    history = CycleHistory()
    base = timestamp()

    history.record_cycle(_make_cycle(base, 100.0))

    statistics = history.statistics
    assert statistics.flow_return_delta.daily.p50 == 10.0
    assert statistics.flow_control_setpoint_error.daily.p50 == 0.0
