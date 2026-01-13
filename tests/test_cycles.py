"""Tests for cycle tracking and classification."""

from typing import Optional

import pytest

from custom_components.sat.boiler import BoilerState, BoilerControlIntent
from custom_components.sat.coordinator import ControlLoopSample
from custom_components.sat.cycles import Cycle, CycleHistory, CycleMetrics, CycleShapeMetrics, CycleTracker
from custom_components.sat.cycles.const import (
    OVERSHOOT_MARGIN_CELSIUS,
    UNDERSHOOT_MARGIN_CELSIUS,
    DEFAULT_DUTY_WINDOW_SECONDS,
    DEFAULT_CYCLES_WINDOW_SECONDS,
    TARGET_MIN_ON_TIME_SECONDS,
    ULTRA_SHORT_MIN_ON_TIME_SECONDS,
)
from custom_components.sat.const import COLD_SETPOINT
from custom_components.sat.helpers import timestamp
from custom_components.sat.pwm import PWMState
from custom_components.sat.types import CycleClassification, CycleKind, Percentiles, PWMStatus


def _make_pwm_state(status: PWMStatus = PWMStatus.IDLE) -> PWMState:
    return PWMState(
        enabled=True,
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
) -> BoilerState:
    return BoilerState(
        flame_active=flame_active,
        central_heating=central_heating,
        hot_water_active=hot_water_active,
        flame_on_since=None,
        flame_off_since=None,
        setpoint=setpoint,
        flow_temperature=flow_temperature,
        return_temperature=flow_temperature - 10.0,
        relative_modulation_level=None,
        max_modulation_level=100,
        modulation_reliable=True
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
        state=_make_boiler_state(
            flame_active=flame_active,
            setpoint=setpoint,
            flow_temperature=flow_temperature,
            hot_water_active=hot_water_active,
        ),
        intent=BoilerControlIntent(setpoint=setpoint, relative_modulation=None),
        outside_temperature=5.0,
        requested_setpoint=setpoint,
    )


def _tail_metrics_for_error(error: Optional[float], *, hot_water_fraction: float = 0.0) -> CycleMetrics:
    return CycleMetrics(
        setpoint=Percentiles(p50=40.0, p90=40.0),
        intent_setpoint=Percentiles(p50=40.0, p90=40.0),
        flow_temperature=Percentiles(p50=40.0, p90=40.0),
        return_temperature=Percentiles(p50=30.0, p90=30.0),
        relative_modulation_level=Percentiles(p50=None, p90=None),
        flow_return_delta=Percentiles(p50=10.0, p90=10.0),
        flow_setpoint_error=Percentiles(p50=error, p90=error),
        hot_water_active_fraction=hot_water_fraction,
    )


def _shape_metrics(duration: float) -> CycleShapeMetrics:
    return CycleShapeMetrics(
        total_overshoot_seconds=0,
        max_flow_setpoint_error=0,
        time_in_band_seconds=duration,
        time_to_first_overshoot_seconds=None,
        time_to_sustained_overshoot_seconds=duration,
    )


def _make_cycle(end_time: float, duration: float) -> Cycle:
    metrics = _tail_metrics_for_error(0.0)
    return Cycle(
        kind=CycleKind.CENTRAL_HEATING,
        tail=metrics,
        metrics=metrics,
        shape=_shape_metrics(duration),
        classification=CycleClassification.GOOD,
        start=end_time - duration,
        end=end_time,
        sample_count=3,
        max_flow_temperature=45.0,
        fraction_space_heating=1.0,
        fraction_domestic_hot_water=0.0,
    )


def test_classify_premature_off_when_pwm_on():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.ON)
    tail_metrics = _tail_metrics_for_error(0.0)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.PREMATURE_OFF


def test_classify_fast_overshoot():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(OVERSHOOT_MARGIN_CELSIUS + 0.1)

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=ULTRA_SHORT_MIN_ON_TIME_SECONDS - 1.0,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.FAST_OVERSHOOT


def test_classify_too_short_underheat():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(-(UNDERSHOOT_MARGIN_CELSIUS + 0.2))

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS * 0.5,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.TOO_SHORT_UNDERHEAT


def test_classify_long_underheat():
    boiler_state = _make_boiler_state(flame_active=False)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(-(UNDERSHOOT_MARGIN_CELSIUS + 0.3))

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.LONG_UNDERHEAT


def test_classify_long_underheat_below_cold_setpoint():
    boiler_state = _make_boiler_state(flame_active=False, setpoint=COLD_SETPOINT - 5.0)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(-(UNDERSHOOT_MARGIN_CELSIUS + 0.3))

    classification = CycleTracker._classify_cycle(
        boiler_state=boiler_state,
        duration_seconds=TARGET_MIN_ON_TIME_SECONDS + 10.0,
        kind=CycleKind.CENTRAL_HEATING,
        pwm_state=pwm_state,
        tail_metrics=tail_metrics,
    )

    assert classification is CycleClassification.LONG_UNDERHEAT


def test_classify_short_underheat_below_cold_setpoint_uncertain():
    boiler_state = _make_boiler_state(flame_active=False, setpoint=COLD_SETPOINT - 5.0)
    pwm_state = _make_pwm_state(PWMStatus.IDLE)
    tail_metrics = _tail_metrics_for_error(-(UNDERSHOOT_MARGIN_CELSIUS + 0.2))

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

    assert history.cycles_last_hour == pytest.approx(3.0 * 3600.0 / DEFAULT_CYCLES_WINDOW_SECONDS, abs=0.01)
    assert history.duty_ratio_last_15m == pytest.approx(300.0 / DEFAULT_DUTY_WINDOW_SECONDS, abs=0.01)
