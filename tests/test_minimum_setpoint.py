import pytest

from custom_components.sat.boiler import BoilerCapabilities
from custom_components.sat.cycles import (
    Cycle,
    CycleMetrics,
    CycleShapeMetrics,
    CycleStatistics,
    CycleWindowStats,
    TARGET_MIN_ON_TIME_SECONDS,
)
from custom_components.sat.minimum_setpoint import (
    DynamicMinimumSetpoint,
    MinimumSetpointConfig,
    RegimeKey,
    RegimeState,
)
from custom_components.sat.types import CycleClassification, CycleKind, Percentiles


def _make_metrics(setpoint: float, error: float) -> CycleMetrics:
    return CycleMetrics(
        setpoint=Percentiles(p50=setpoint, p90=setpoint),
        intent_setpoint=Percentiles(p50=setpoint, p90=setpoint),
        flow_temperature=Percentiles(p50=setpoint + error, p90=setpoint + error),
        return_temperature=Percentiles(p50=35.0, p90=35.0),
        relative_modulation_level=Percentiles(p50=30.0, p90=30.0),
        flow_return_delta=Percentiles(p50=5.0, p90=5.0),
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


def test_learning_band_expands_early() -> None:
    controller = DynamicMinimumSetpoint(MinimumSetpointConfig(minimum_setpoint=30.0, maximum_setpoint=80.0))
    controller._active_regime = _make_regime(minimum_setpoint=40.0, completed_cycles=0)

    cycle = _make_cycle(CycleClassification.FAST_OVERSHOOT, setpoint=44.0, error=2.5)
    controller._maybe_tune_minimum(BoilerCapabilities(minimum_setpoint=30.0, maximum_setpoint=80.0), _make_stats(), cycle)

    assert controller._active_regime.minimum_setpoint > 40.0


def test_early_step_scaling_accelerates_adjustment() -> None:
    controller = DynamicMinimumSetpoint(MinimumSetpointConfig(minimum_setpoint=30.0, maximum_setpoint=80.0))
    controller._active_regime = _make_regime(minimum_setpoint=50.0, completed_cycles=0)

    cycle = _make_cycle(CycleClassification.FAST_UNDERHEAT, setpoint=50.0, error=-2.5)
    controller._maybe_tune_minimum(BoilerCapabilities(minimum_setpoint=30.0, maximum_setpoint=80.0), _make_stats(), cycle)

    assert controller._active_regime.minimum_setpoint == pytest.approx(48.7, abs=0.01)
