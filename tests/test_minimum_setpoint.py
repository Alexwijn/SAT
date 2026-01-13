from typing import Optional

import pytest

from custom_components.sat.boiler import BoilerCapabilities, BoilerControlIntent, BoilerState
from custom_components.sat.coordinator import ControlLoopSample
from custom_components.sat.const import MINIMUM_SETPOINT
from custom_components.sat.cycles import Cycle, CycleMetrics, CycleShapeMetrics, CycleStatistics, CycleWindowStats
from custom_components.sat.cycles.const import TARGET_MIN_ON_TIME_SECONDS
from custom_components.sat.minimum_setpoint import DynamicMinimumSetpoint, MinimumSetpointConfig, RegimeKey, RegimeSeeder, RegimeState
from custom_components.sat.minimum_setpoint.const import DELTA_BAND_LOW, DELTA_BAND_MED, OUTSIDE_BAND_COLD, OUTSIDE_BAND_MILD
from custom_components.sat.minimum_setpoint.tuner import MinimumSetpointTuner
from custom_components.sat.pwm import PWMState
from custom_components.sat.types import CycleClassification, CycleKind, Percentiles, PWMStatus


def _make_metrics(setpoint: float, error: float) -> CycleMetrics:
    return CycleMetrics(
        setpoint=Percentiles(p50=setpoint, p90=setpoint),
        intent_setpoint=Percentiles(p50=setpoint, p90=setpoint),
        flow_temperature=Percentiles(p50=setpoint + error, p90=setpoint + error),
        return_temperature=Percentiles(p50=35.0, p90=35.0),
        relative_modulation_level=Percentiles(p50=30.0, p90=30.0),
        flow_return_delta=Percentiles(p50=12.0, p90=12.0),
        flow_setpoint_error=Percentiles(p50=error, p90=error),
        hot_water_active_fraction=0.0,
    )


def _make_cycle(classification: CycleClassification, *, setpoint: float, error: float) -> Cycle:
    duration = TARGET_MIN_ON_TIME_SECONDS + 10.0
    metrics = _make_metrics(setpoint, error)
    return Cycle(
        kind=CycleKind.CENTRAL_HEATING,
        tail=metrics,
        metrics=metrics,
        shape=CycleShapeMetrics(
            time_in_band_seconds=duration,
            time_to_first_overshoot_seconds=None,
            time_to_sustained_overshoot_seconds=None,
            total_overshoot_seconds=0.0,
            max_flow_setpoint_error=error,
        ),
        classification=classification,
        start=0.0,
        end=duration,
        sample_count=10,
        max_flow_temperature=50.0,
        fraction_space_heating=1.0,
        fraction_domestic_hot_water=0.0,
    )


def _make_stats() -> CycleStatistics:
    return CycleStatistics(
        window=CycleWindowStats(
            sample_count_4h=3,
            last_hour_count=3.0,
            duty_ratio_last_15m=0.3,
            off_with_demand_duration=None,
            median_on_duration_seconds_4h=None,
        ),
        flow_return_delta=Percentiles(p50=5.0, p90=5.0),
        flow_setpoint_error=Percentiles(p50=0.0, p90=0.0),
    )


def _make_regime(*, minimum_setpoint: float, completed_cycles: int) -> RegimeState:
    return RegimeState(
        key=RegimeKey(setpoint_band=0, outside_band="cold", delta_band="d_low"),
        minimum_setpoint=minimum_setpoint,
        completed_cycles=completed_cycles,
        stable_cycles=0,
    )


def _make_sample(*, requested_setpoint: Optional[float], intent_setpoint: Optional[float], hot_water_active: bool = False) -> ControlLoopSample:
    return ControlLoopSample(
        timestamp=0.0,
        pwm=PWMState(
            enabled=True,
            status=PWMStatus.IDLE,
            duty_cycle=None,
            last_duty_cycle_percentage=None,
        ),
        state=BoilerState(
            flame_active=True,
            central_heating=True,
            hot_water_active=hot_water_active,
            modulation_reliable=True,
            flame_on_since=None,
            flame_off_since=None,
            setpoint=intent_setpoint,
            flow_temperature=intent_setpoint if intent_setpoint is not None else 40.0,
            return_temperature=(intent_setpoint - 5.0) if intent_setpoint is not None else 35.0,
            max_modulation_level=100,
            relative_modulation_level=30.0,
        ),
        intent=BoilerControlIntent(setpoint=intent_setpoint, relative_modulation=None),
        outside_temperature=10.0,
        requested_setpoint=requested_setpoint,
    )


def test_regime_key_prefers_requested_setpoint() -> None:
    controller = DynamicMinimumSetpoint(MinimumSetpointConfig(minimum_setpoint=30.0, maximum_setpoint=80.0))
    sample = _make_sample(requested_setpoint=36.0, intent_setpoint=10.0)

    controller.on_cycle_start(
        boiler_capabilities=BoilerCapabilities(minimum_setpoint=30.0, maximum_setpoint=80.0),
        sample=sample,
    )

    assert controller.active_regime is not None
    assert controller.active_regime.key.setpoint_band == 9


def test_regime_skips_hot_water_cycles() -> None:
    controller = DynamicMinimumSetpoint(MinimumSetpointConfig(minimum_setpoint=30.0, maximum_setpoint=80.0))
    sample = _make_sample(requested_setpoint=36.0, intent_setpoint=36.0, hot_water_active=True)

    controller.on_cycle_start(
        boiler_capabilities=BoilerCapabilities(minimum_setpoint=30.0, maximum_setpoint=80.0),
        sample=sample,
    )

    assert controller.active_regime is None


def test_regime_skips_forced_minimum_without_request() -> None:
    controller = DynamicMinimumSetpoint(MinimumSetpointConfig(minimum_setpoint=30.0, maximum_setpoint=80.0))
    sample = _make_sample(requested_setpoint=None, intent_setpoint=MINIMUM_SETPOINT)

    controller.on_cycle_start(
        boiler_capabilities=BoilerCapabilities(minimum_setpoint=30.0, maximum_setpoint=80.0),
        sample=sample,
    )

    assert controller.active_regime is None


def test_learning_band_expands_early() -> None:
    controller = DynamicMinimumSetpoint(MinimumSetpointConfig(minimum_setpoint=30.0, maximum_setpoint=80.0))
    controller._active_regime = _make_regime(minimum_setpoint=40.0, completed_cycles=0)

    cycle = _make_cycle(CycleClassification.FAST_OVERSHOOT, setpoint=44.0, error=2.5)
    MinimumSetpointTuner.tune(
        boiler_capabilities=BoilerCapabilities(minimum_setpoint=30.0, maximum_setpoint=80.0),
        cycles=_make_stats(),
        cycle=cycle,
        regime_state=controller._active_regime,
    )

    assert controller._active_regime.minimum_setpoint > 40.0


def test_early_step_scaling_accelerates_adjustment() -> None:
    controller = DynamicMinimumSetpoint(MinimumSetpointConfig(minimum_setpoint=30.0, maximum_setpoint=80.0))
    controller._active_regime = _make_regime(minimum_setpoint=50.0, completed_cycles=0)

    cycle = _make_cycle(CycleClassification.FAST_UNDERHEAT, setpoint=50.0, error=-2.5)
    MinimumSetpointTuner.tune(
        boiler_capabilities=BoilerCapabilities(minimum_setpoint=30.0, maximum_setpoint=80.0),
        cycles=_make_stats(),
        cycle=cycle,
        regime_state=controller._active_regime,
    )

    assert controller._active_regime.minimum_setpoint == pytest.approx(48.7, abs=0.01)


def test_initial_minimum_blends_nearby_regimes() -> None:
    controller = DynamicMinimumSetpoint(MinimumSetpointConfig(minimum_setpoint=30.0, maximum_setpoint=80.0))
    controller._value = None

    active_key = RegimeKey(setpoint_band=5, outside_band=OUTSIDE_BAND_COLD, delta_band=DELTA_BAND_LOW)

    controller._regimes = {
        RegimeKey(setpoint_band=5, outside_band=OUTSIDE_BAND_COLD, delta_band=DELTA_BAND_LOW): RegimeState(
            key=RegimeKey(setpoint_band=5, outside_band=OUTSIDE_BAND_COLD, delta_band=DELTA_BAND_LOW),
            minimum_setpoint=40.0,
            completed_cycles=3,
            stable_cycles=2,
        ),
        RegimeKey(setpoint_band=6, outside_band=OUTSIDE_BAND_COLD, delta_band=DELTA_BAND_LOW): RegimeState(
            key=RegimeKey(setpoint_band=6, outside_band=OUTSIDE_BAND_COLD, delta_band=DELTA_BAND_LOW),
            minimum_setpoint=50.0,
            completed_cycles=4,
            stable_cycles=3,
        ),
        RegimeKey(setpoint_band=5, outside_band=OUTSIDE_BAND_MILD, delta_band=DELTA_BAND_MED): RegimeState(
            key=RegimeKey(setpoint_band=5, outside_band=OUTSIDE_BAND_MILD, delta_band=DELTA_BAND_MED),
            minimum_setpoint=60.0,
            completed_cycles=3,
            stable_cycles=2,
        ),
    }

    initial = RegimeSeeder.initial_minimum(active_key, controller._regimes, controller._value)

    assert initial == pytest.approx(46.4, abs=0.05)
