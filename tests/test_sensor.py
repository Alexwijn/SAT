"""Tests for SAT sensor entities."""

import pytest

from custom_components.sat.device import DeviceState
from custom_components.sat.const import EVENT_SAT_CYCLE_ENDED, CONF_HEATING_SYSTEM
from custom_components.sat.cycles import Cycle, CycleMetrics, CycleShapeMetrics
from custom_components.sat.heating_control import ControlLoopSample
from custom_components.sat.helpers import timestamp
from custom_components.sat.pwm import PWMState
from custom_components.sat.types import CycleClassification, CycleControlMode, CycleKind, Percentiles, PWMStatus, HeatingSystem
from homeassistant.helpers import entity_registry as er

pytestmark = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [
        (
            [],
            {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS},
            {},
            {},
        ),
    ],
)


def _metrics(error: float, *, hot_water_fraction: float = 0.0) -> CycleMetrics:
    return CycleMetrics(
        requested_setpoint=Percentiles(p50=45.0, p90=45.0),
        control_setpoint=Percentiles(p50=45.0, p90=46.0),
        flow_temperature=Percentiles(p50=44.0, p90=47.0),
        return_temperature=Percentiles(p50=38.0, p90=39.0),
        relative_modulation_level=Percentiles(p50=30.0, p90=40.0),
        flow_return_delta=Percentiles(p50=6.0, p90=7.0),
        flow_control_setpoint_error=Percentiles(p50=error, p90=error),
        flow_requested_setpoint_error=Percentiles(p50=error, p90=error),
        hot_water_active_fraction=hot_water_fraction,
    )


def _shape_metrics(duration: float) -> CycleShapeMetrics:
    return CycleShapeMetrics(
        time_in_band_seconds=duration,
        time_to_first_overshoot_seconds=None,
        time_to_sustained_overshoot_seconds=None,
        total_overshoot_seconds=0.0,
        max_flow_control_setpoint_error=0.0,
    )


def _make_sample(sample_time: float) -> ControlLoopSample:
    return ControlLoopSample(
        timestamp=sample_time,
        pwm=PWMState(
            enabled=True,
            status=PWMStatus.IDLE,
            duty_cycle=None,
            last_duty_cycle_percentage=None,
        ),
        device_state=DeviceState(
            flame_active=True,
            central_heating=True,
            hot_water_active=False,
            setpoint=45.0,
            flow_temperature=40.0,
            return_temperature=35.0,
            max_modulation_level=100,
            relative_modulation_level=30.0,
        ),
        control_setpoint=45.0,
        relative_modulation=None,
        outside_temperature=5.0,
        requested_setpoint=45.0,
    )


async def test_cycle_sensor_extra_attributes(hass, climate, coordinator, entry, domains, data, options, config):
    end_time = timestamp()
    duration = 240.0
    metrics = _metrics(0.2)

    cycle = Cycle(
        kind=CycleKind.CENTRAL_HEATING,
        control_mode=CycleControlMode.CONTINUOUS,
        tail=metrics,
        metrics=metrics,
        shape=_shape_metrics(duration),
        classification=CycleClassification.GOOD,
        start=end_time - duration,
        end=end_time,
        sample_count=5,
        min_flow_temperature=35.0,
        max_flow_temperature=50.0,
        fraction_space_heating=1.0,
        fraction_domestic_hot_water=0.0,
    )

    climate._heating_control._cycles.record_cycle(cycle)
    hass.bus.async_fire(EVENT_SAT_CYCLE_ENDED, {"cycle": cycle})
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-cycle-status")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == CycleClassification.GOOD.name

    attrs = state.attributes
    assert attrs["kind"] == CycleKind.CENTRAL_HEATING.name
    assert attrs["duration_seconds"] == round(duration, 1)
    assert attrs["sample_count"] == 5
    assert attrs["max_flow_temperature"] == 50.0
    assert attrs["fraction_space_heating"] == 1.0
    assert attrs["fraction_domestic_hot_water"] == 0.0
    assert attrs["tail_hot_water_active_fraction"] == 0.0
    assert attrs["tail_flow_control_setpoint_error_p90"] == 0.2
    assert attrs["tail_flow_requested_setpoint_error_p90"] == 0.2
    assert attrs["tail_flow_temperature_p90"] == 47.0
    assert attrs["tail_control_setpoint_p50"] == 45.0
    assert attrs["tail_requested_setpoint_p50"] == 45.0


async def test_core_sensors_registered(hass, climate, entry, domains, data, options, config):
    registry = er.async_get(hass)
    unique_id_prefix = entry.entry_id
    expected_unique_ids = (
        f"{unique_id_prefix}-pid",
        f"{unique_id_prefix}-error-value",
        f"{unique_id_prefix}-heating-curve",
        f"{unique_id_prefix}-boiler-status",
        f"{unique_id_prefix}-manufacturer",
        f"{unique_id_prefix}-cycle-status",
        f"{unique_id_prefix}-requested-setpoint",
    )

    for unique_id in expected_unique_ids:
        entity_id = registry.async_get_entity_id("sensor", "sat", unique_id)
        assert entity_id is not None
