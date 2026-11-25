"""Tests for SAT sensor entities."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest

from custom_components.sat.device import DeviceState
from custom_components.sat.const import EVENT_SAT_CYCLE_ENDED, CONF_HEATING_SYSTEM, SIGNAL_PID_UPDATED
from custom_components.sat.cycles import Cycle, CycleMetrics, CycleShapeMetrics
from custom_components.sat.diagnostics import async_get_config_entry_diagnostics
from custom_components.sat.efficiency import EfficiencyPeriod, EfficiencyReport
from custom_components.sat.seasonal_curve import SeasonalCurveEvaluator, SeasonalCurveHour
from custom_components.sat.sensor import BOILER_STATUS_ICONS
from custom_components.sat.heating_control import ControlLoopSample
from custom_components.sat.helpers import timestamp
from custom_components.sat.pwm import PWMState
from custom_components.sat.types import BoilerStatus, CycleClassification, CycleControlMode, CycleKind, Percentiles, PWMStatus, HeatingSystem
from custom_components.sat.wind import WindReading
from custom_components.sat.wind_sensitivity import WindDay
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

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


async def test_cycle_sensor_extra_attributes(hass, climate, demand_control, coordinator, entry, domains, data, options, config):
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

    demand_control._heating_control._cycles.record_cycle(cycle)
    hass.bus.async_fire(EVENT_SAT_CYCLE_ENDED, {"cycle": cycle})
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-cycle-status")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == CycleClassification.GOOD.name
    assert state.attributes["icon"] == "mdi:check-circle"

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
        f"{unique_id_prefix}-heating-curve-recommendation",
    )

    for unique_id in expected_unique_ids:
        entity_id = registry.async_get_entity_id("sensor", "sat", unique_id)
        assert entity_id is not None


async def test_measurement_sensors_reach_long_term_statistics(hass, climate, entry, domains, data, options, config):
    registry = er.async_get(hass)

    for suffix in ("pid", "error-value", "heating-curve", "requested-setpoint", "outside-temperature", "wind-speed", "implied-curve-coefficient"):
        entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-{suffix}")
        assert registry.async_get(entity_id).capabilities == {"state_class": "measurement"}

    for suffix in ("boiler-status", "manufacturer", "cycle-status", "heating-curve-recommendation"):
        entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-{suffix}")
        assert "state_class" not in (registry.async_get(entity_id).capabilities or {})


async def test_implied_coefficient_is_unknown_outside_steady_heating(hass, climate, entry, domains, data, options, config):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-implied-curve-coefficient")

    assert hass.states.get(entity_id).state == "unknown"


async def test_implied_coefficient_keeps_the_last_measured_value(hass, climate, demand_control, entry, domains, data, options, config, monkeypatch):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-implied-curve-coefficient")

    monkeypatch.setattr(type(demand_control), "implied_curve_coefficient", property(lambda self: 2.3))
    async_dispatcher_send(hass, SIGNAL_PID_UPDATED, demand_control.id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "2.3"
    assert state.attributes["last_value"] == 2.3
    measured_at = state.attributes["last_measured_at"]
    assert measured_at is not None

    monkeypatch.setattr(type(demand_control), "implied_curve_coefficient", property(lambda self: None))
    async_dispatcher_send(hass, SIGNAL_PID_UPDATED, demand_control.id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "unknown"
    assert state.attributes["last_value"] == 2.3
    assert state.attributes["last_measured_at"] == measured_at


async def test_implied_coefficient_restores_the_last_measured_value(hass, climate, entry, domains, data, options, config, monkeypatch):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-implied-curve-coefficient")
    sensor = hass.data["sensor"].get_entity(entity_id)

    last_state = State(entity_id, "unknown", {"last_value": 1.9, "last_measured_at": "2026-11-10T20:00:00+00:00"})
    monkeypatch.setattr(sensor, "async_get_last_state", AsyncMock(return_value=last_state))
    await sensor.async_added_to_hass()
    sensor.async_write_ha_state()

    state = hass.states.get(entity_id)
    assert state.state == "unknown"
    assert state.attributes["last_value"] == 1.9
    assert state.attributes["last_measured_at"] == datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)


async def test_seasonal_coefficient_sensor_exposes_the_fit(hass, climate, demand_control, coordinator, entry, domains, data, options, config):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-seasonal-curve-coefficient")

    assert hass.states.get(entity_id).state == "unknown"
    assert hass.states.get(entity_id).attributes["hours"] == 0

    hours = [SeasonalCurveHour(start=index * 21600.0, coefficient=2.4, outside_temperature=4.0) for index in range(60)]
    demand_control.seasonal_curve._fit = SeasonalCurveEvaluator.fit(hours)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "2.4"
    assert state.attributes["hours"] == 60
    assert state.attributes["minimum_days"] == 5
    assert state.attributes["minimum_hours"] == 48
    assert state.attributes["curve_mismatch"] is False
    assert state.attributes["bins"] == [{"minimum_temperature": 3, "coefficient": 2.4, "hours": 60}]


async def test_efficiency_sensor_exposes_the_report(hass, climate, demand_control, coordinator, entry, domains, data, options, config):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-heating-efficiency")

    assert hass.states.get(entity_id).state == "unknown"
    assert hass.states.get(entity_id).attributes["source"] is None

    current = EfficiencyPeriod(days=365, baseload=1.0, degree_days=1800.0, gas_per_degree_day=0.55)
    previous = EfficiencyPeriod(days=365, baseload=1.0, degree_days=1900.0, gas_per_degree_day=0.5)
    demand_control.efficiency._report = EfficiencyReport(source="meter", unit="m³", current=current, previous=previous, gas_estimate_ratio=1.2)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "0.55"
    assert state.attributes["unit_of_measurement"] == "m³/°Cd"
    assert state.attributes["source"] == "meter"
    assert state.attributes["baseload_per_day"] == 1.0
    assert state.attributes["degree_days"] == 1800.0
    assert state.attributes["previous_year"] == 0.5
    assert state.attributes["change_percentage"] == 10.0
    assert state.attributes["gas_estimate_ratio"] == 1.2


async def test_diagnostics_include_the_efficiency_report(hass, climate, demand_control, entry, domains, data, options, config):
    demand_control.efficiency._report = EfficiencyReport(source="estimate", unit="m³", current=EfficiencyPeriod(days=365, baseload=1.0, degree_days=1800.0, gas_per_degree_day=0.5))

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["climate"]["efficiency"]["source"] == "estimate"
    assert diagnostics["climate"]["efficiency"]["current"]["gas_per_degree_day"] == 0.5
    assert diagnostics["climate"]["efficiency"]["change_percentage"] is None


async def test_diagnostics_include_the_seasonal_fit(hass, climate, entry, domains, data, options, config):
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["climate"]["seasonal_curve"]["coefficient"] is None
    assert diagnostics["climate"]["seasonal_curve"]["curve_mismatch"] is False


async def test_outside_temperature_sensor_mirrors_the_climate(hass, climate, demand_control, entry, domains, data, options, config, monkeypatch):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-outside-temperature")

    monkeypatch.setattr(type(demand_control), "current_outside_temperature", property(lambda self: 4.26))
    demand_control.async_update_listeners()
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "4.3"


async def test_wind_speed_sensor_mirrors_the_climate(hass, climate, demand_control, entry, domains, data, options, config, monkeypatch):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-wind-speed")

    monkeypatch.setattr(type(demand_control), "current_wind", property(lambda self: WindReading(speed=23.4, entity_id="weather.home")))
    demand_control.async_update_listeners()
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "23.4"
    assert state.attributes["unit_of_measurement"] == "km/h"
    assert state.attributes["source"] == "weather.home"


async def test_heating_curve_recommendation_shows_the_wind_sensitivity(hass, climate, demand_control, coordinator, entry, domains, data, options, config):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", "sat", f"{entry.entry_id}-heating-curve-recommendation")
    speeds = [2.0, 5.0, 10.0, 18.0, 30.0, 42.0, 55.0, 60.0]
    demand_control.wind_sensitivity.update([WindDay(date=f"2026-11-{index + 1:02d}", wind_speed=speed, coefficient=2.3 * (1 + 0.005 * speed), sample_count=100) for index, speed in enumerate(speeds)], date(2026, 11, 20))

    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    attributes = hass.states.get(entity_id).attributes
    assert attributes["wind_days"] == 8
    assert attributes["calm_coefficient"] == pytest.approx(2.3, abs=0.01)
    assert attributes["wind_sensitivity_percent_per_10_kmh"] == pytest.approx(5.0, abs=0.1)


async def test_wind_speed_sensor_is_unknown_without_wind(hass, climate, entry, domains, data, options, config):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", "sat", f"{entry.entry_id}-wind-speed")

    assert hass.states.get(entity_id).state == "unknown"


async def test_device_status_sensor_follows_heating_control(hass, heating_control, entry, domains, data, options, config):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "sat", f"{entry.entry_id}-boiler-status")

    assert entity_id is not None
    assert hass.states.get(entity_id).state == heating_control.device_status.name

    heating_control._device_tracker._current_status = BoilerStatus.CENTRAL_HEATING
    heating_control._handle_coordinator_update()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == heating_control.device_status.name
    assert hass.states.get(entity_id).attributes["icon"] == BOILER_STATUS_ICONS[heating_control.device_status]
