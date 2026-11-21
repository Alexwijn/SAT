"""Tests focused on SAT climate setpoint and heating curve behavior."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import State, is_callback
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sat.entry_data import SatConfig
from custom_components.sat.const import (
    COLD_SETPOINT,
    HEATING_CONTROL_SCHEDULE_TIMEOUT_SECONDS,
    CONF_HEATING_CURVE_COEFFICIENT,
    CONF_HEATING_MODE,
    CONF_SOLAR_GAIN_COMPENSATION,
    CONF_SOLAR_GAIN_FREEZE_INTEGRAL,
    CONF_SOLAR_GAIN_MIN_ELEVATION,
    CONF_SOLAR_GAIN_MIN_RISE_PER_HOUR,
    CONF_SOLAR_GAIN_SETPOINT_OFFSET_CELSIUS,
    CONF_SENSOR_MAX_VALUE_AGE,
    CONF_HEATING_SYSTEM,
    CONF_PUSH_SETPOINT_TO_THERMOSTAT,
    CONF_THERMOSTAT,
    DOMAIN,
    HEATING_CURVE_SETTLE_SECONDS,
    HeatingMode,
    HeatingSystem,
    MINIMUM_SETPOINT,
    OPTIONS_DEFAULTS,
    SERVICE_PULSE_WIDTH_MODULATION,
    SERVICE_RESET_INTEGRAL,
)
from custom_components.sat.area import DrivingRoom
from custom_components.sat.climate import SatClimate
from custom_components.sat.heating_curve import HeatingCurve, HeatingCurveAdviceSample
from custom_components.sat.helpers import timestamp
from custom_components.sat.temperature.history import TemperatureHistory
from custom_components.sat.temperature.state import TemperatureState
from custom_components.sat.manufacturer import ManufacturerFactory
from custom_components.sat.pid import PID
from custom_components.sat.room_balance import RoomSignalReading, RoomSignals
from custom_components.sat.types import HeaterState
from custom_components.sat.solar_gain import SolarGainController, SolarGainSnapshot
from custom_components.sat.wind import WindHistory, WindReading
from custom_components.sat.wind_sensitivity import WindDay

pytestmark = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [
        (
            [],
            {
                CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
            },
            {},
            {},
        ),
    ],
)


def _update_climate_config(demand_control, *, data=None, options=None, climate=None) -> None:
    current_data = dict(demand_control._config.data)
    current_options = dict(demand_control._config.options)

    if data:
        current_data.update(data)
    if options:
        current_options.update(options)

    new_config = SatConfig(demand_control._config.entry_id, current_data, {**OPTIONS_DEFAULTS, **current_options})
    demand_control._config = new_config
    demand_control._coordinator._config = new_config
    demand_control._coordinator._manufacturer = ManufacturerFactory.resolve_by_name(new_config.manufacturer) if new_config.manufacturer else None
    demand_control._solar_gain = SolarGainController(new_config.solar_gain)

    if climate is not None:
        climate._config = new_config


def test_requested_setpoint_without_heating_curve(demand_control):
    assert demand_control.heating_curve.value is None
    assert demand_control.requested_setpoint == MINIMUM_SETPOINT


def test_requested_setpoint_eco_mode_ignores_secondary_and_caps(monkeypatch, demand_control):
    _update_climate_config(demand_control, options={CONF_HEATING_MODE: HeatingMode.ECO})
    demand_control.heating_curve._value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 42.44))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 55.0))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 35.0))

    assert demand_control.requested_setpoint == 42.4


def test_requested_setpoint_uses_secondary_and_cap(monkeypatch, demand_control):
    _update_climate_config(demand_control, options={CONF_HEATING_MODE: HeatingMode.COMFORT})
    demand_control.heating_curve._value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 40.2))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 45.6))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 43.1))

    assert demand_control.requested_setpoint == 43.1


def test_requested_setpoint_is_not_capped_below_a_cold_main_room(monkeypatch, demand_control):
    _update_climate_config(demand_control, options={CONF_HEATING_MODE: HeatingMode.COMFORT})
    demand_control.heating_curve._value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 42.0))
    monkeypatch.setattr(type(demand_control), "temperature_state", property(lambda self: SimpleNamespace(error=1.0)))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 35.0))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 30.0))

    assert demand_control.requested_setpoint == 42.0


def test_requested_setpoint_applies_solar_gain_offset(monkeypatch, demand_control):
    _update_climate_config(demand_control, options={
        CONF_SOLAR_GAIN_COMPENSATION: True,
        CONF_SOLAR_GAIN_SETPOINT_OFFSET_CELSIUS: 2.0,
    })
    demand_control.heating_curve._value = 30.0
    demand_control._solar_gain_snapshot = SolarGainSnapshot(active=True, rise_per_hour=0.8, sun_elevation=20.0)

    monkeypatch.setattr(PID, "output", property(lambda self: 41.2))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: None))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: None))

    assert demand_control.requested_setpoint == 39.2


def test_update_heating_curves_updates_value(hass, demand_control):
    hass.states.async_set("sensor.test_inside_sensor", "20")
    hass.states.async_set("sensor.test_outside_sensor", "5")
    demand_control._target_temperature = 21.0

    demand_control._update_heating_curves()

    base_offset = HeatingSystem.RADIATORS.base_offset
    coefficient = float(OPTIONS_DEFAULTS[CONF_HEATING_CURVE_COEFFICIENT])
    expected_curve = HeatingCurve.calculate(21.0, 5.0)
    expected_value = round(base_offset + ((coefficient / 4) * expected_curve), 1)

    assert demand_control.heating_curve.value == expected_value


async def test_set_target_temperature_updates_heating_curve(hass, demand_control):
    hass.states.async_set("sensor.test_outside_sensor", "5")

    demand_control.set_target_temperature(21.0)

    base_offset = HeatingSystem.RADIATORS.base_offset
    coefficient = float(OPTIONS_DEFAULTS[CONF_HEATING_CURVE_COEFFICIENT])
    expected_curve = HeatingCurve.calculate(21.0, 5.0)
    expected_value = round(base_offset + ((coefficient / 4) * expected_curve), 1)

    assert demand_control.heating_curve.value == expected_value


async def test_area_climate_change_updates_heating_curve(hass, demand_control):
    hass.states.async_set("sensor.test_outside_sensor", "5")
    demand_control._target_temperature = 21.0

    new_state = State("demand_control.room", HVACMode.HEAT, {"temperature": 22.0})
    event = SimpleNamespace(data={"new_state": new_state})

    await demand_control._async_climate_changed(event)

    base_offset = HeatingSystem.RADIATORS.base_offset
    coefficient = float(OPTIONS_DEFAULTS[CONF_HEATING_CURVE_COEFFICIENT])
    expected_curve = HeatingCurve.calculate(21.0, 5.0)
    expected_value = round(base_offset + ((coefficient / 4) * expected_curve), 1)

    assert demand_control.heating_curve.value == expected_value


def test_control_pid_resets_on_stale_inside_sensor(monkeypatch, hass, demand_control):
    _update_climate_config(demand_control, options={CONF_SENSOR_MAX_VALUE_AGE: "00:01:00"})
    demand_control._target_temperature = 21.0

    now = dt_util.utcnow()
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    hass.states.async_set("sensor.test_inside_sensor", "20")
    hass.states.async_set("sensor.test_outside_sensor", "5")

    monkeypatch.setattr(dt_util, "utcnow", lambda: now + timedelta(seconds=120))

    reset_called = {"value": False}

    def _reset(self):
        reset_called["value"] = True

    monkeypatch.setattr(PID, "reset", _reset)

    demand_control.control_pid()

    assert reset_called["value"] is True


async def test_control_loop_passes_the_target_temperature_change_time(monkeypatch, hass, demand_control):
    hass.states.async_set("sensor.test_outside_sensor", "5")
    demand_control._hvac_mode = HVACMode.HEAT

    update_mock = AsyncMock()
    monkeypatch.setattr(demand_control._heating_control, "update", update_mock)

    demand_control.set_target_temperature(21.0)
    await demand_control.async_control_heating_loop()

    demand = update_mock.call_args.args[0]
    assert demand.target_temperature_changed_at == demand_control._target_temperature_changed_at
    assert demand.target_temperature_changed_at is not None


async def test_control_loop_commands_heater_off_when_hvac_off(monkeypatch, hass, demand_control):
    hass.states.async_set("sensor.test_outside_sensor", "5")
    demand_control._hvac_mode = HVACMode.OFF

    update_mock = AsyncMock()
    monkeypatch.setattr(demand_control._heating_control, "update", update_mock)

    await demand_control.async_control_heating_loop()

    update_mock.assert_called_once()

    demand = update_mock.call_args.args[0]
    assert demand.hvac_mode == HVACMode.OFF
    assert demand.requested_setpoint == MINIMUM_SETPOINT
    assert demand.heater_state == HeaterState.OFF


def test_register_event_listeners_is_idempotent(monkeypatch, demand_control):
    demand_control._event_listeners_registered = False
    calls = {"time_interval": 0, "state_change": 0}

    def _track_time_interval(*args, **kwargs):
        calls["time_interval"] += 1
        return lambda: None

    def _track_state_change_event(*args, **kwargs):
        calls["state_change"] += 1
        return lambda: None

    monkeypatch.setattr("custom_components.sat.demand_control.async_track_time_interval", _track_time_interval)
    monkeypatch.setattr("custom_components.sat.demand_control.async_track_state_change_event", _track_state_change_event)

    demand_control._register_event_listeners()
    first_counts = dict(calls)
    demand_control._register_event_listeners()

    assert calls == first_counts


def test_control_pid_freezes_integral_when_solar_gain_detected(monkeypatch, hass, demand_control):
    _update_climate_config(demand_control, options={
        CONF_SOLAR_GAIN_COMPENSATION: True,
        CONF_SOLAR_GAIN_FREEZE_INTEGRAL: True,
        CONF_SOLAR_GAIN_MIN_ELEVATION: 10.0,
        CONF_SOLAR_GAIN_MIN_RISE_PER_HOUR: 0.5,
    })

    now = dt_util.utcnow()
    demand_control._target_temperature = 21.0
    demand_control._hvac_mode = HVACMode.HEAT
    hass.states.async_set("sensor.test_inside_sensor", "20.0")
    hass.states.async_set("sensor.test_outside_sensor", "5")
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 20.0})
    demand_control._coordinator._relative_modulation_value = 0

    calls: list[bool] = []

    def _update(self, state, freeze_integral=False):
        calls.append(freeze_integral)

    monkeypatch.setattr(PID, "update", _update)
    demand_control.control_pid(now)
    hass.states.async_set("sensor.test_inside_sensor", "20.3")
    demand_control.control_pid(now + timedelta(minutes=15))

    assert calls[-1] is True


async def test_reset_integral_entity_service(hass, climate, demand_control):
    demand_control.pid._integral = 5.0

    await hass.services.async_call(DOMAIN, SERVICE_RESET_INTEGRAL, {"entity_id": climate.entity_id}, blocking=True)

    assert demand_control.pid.integral == 0.0


async def test_pulse_width_modulation_entity_service(hass, climate):
    await hass.services.async_call(DOMAIN, SERVICE_PULSE_WIDTH_MODULATION, {"entity_id": climate.entity_id, "enabled": True}, blocking=True)
    assert climate._heating_control.pwm_state.enabled is True

    await hass.services.async_call(DOMAIN, SERVICE_PULSE_WIDTH_MODULATION, {"entity_id": climate.entity_id, "enabled": False}, blocking=True)
    assert climate._heating_control.pwm_state.enabled is False


async def test_frost_protection_heats_when_off(monkeypatch, hass, demand_control):
    hass.states.async_set("sensor.test_inside_sensor", "4.0")
    hass.states.async_set("sensor.test_outside_sensor", "-5")
    demand_control._hvac_mode = HVACMode.OFF

    update_mock = AsyncMock()
    monkeypatch.setattr(demand_control._heating_control, "update", update_mock)

    await demand_control.async_control_heating_loop()

    demand = update_mock.call_args.args[0]
    assert demand_control._frost_protection_active is True
    assert demand.hvac_mode == HVACMode.HEAT
    assert demand.heater_state == HeaterState.ON


async def test_frost_protection_stays_off_when_warm(monkeypatch, hass, demand_control):
    hass.states.async_set("sensor.test_inside_sensor", "18.0")
    hass.states.async_set("sensor.test_outside_sensor", "-5")
    demand_control._hvac_mode = HVACMode.OFF

    update_mock = AsyncMock()
    monkeypatch.setattr(demand_control._heating_control, "update", update_mock)

    await demand_control.async_control_heating_loop()

    demand = update_mock.call_args.args[0]
    assert demand_control._frost_protection_active is False
    assert demand.heater_state == HeaterState.OFF


def _temperature_state(current: float, setpoint: float) -> TemperatureState:
    now = dt_util.utcnow()
    return TemperatureState(entity_id="sensor.test_inside_sensor", current=current, setpoint=setpoint, last_updated=now, last_changed=now, last_reported=now)


def _prepare_curve_advice(monkeypatch, demand_control, *, proportional: float, derivative: float) -> list:
    recorded: list = []

    demand_control._hvac_mode = HVACMode.HEAT
    demand_control._target_temperature_changed_at = 0.0
    demand_control.heating_curve.update(21.0, 5.0)

    monkeypatch.setattr(type(demand_control.pid), "proportional", property(lambda self: proportional))
    monkeypatch.setattr(type(demand_control.pid), "integral", property(lambda self: 0.5))
    monkeypatch.setattr(type(demand_control.pid), "derivative", property(lambda self: derivative))
    monkeypatch.setattr(demand_control._heating_control, "burned_within_pwm_period", lambda now: True)
    monkeypatch.setattr(demand_control._curve_advisor, "record", recorded.append)
    demand_control._hass.states.async_set("sun.sun", "below_horizon", {"elevation": -10.0})

    for index in range(7):
        demand_control._temperature_history.record(0.1, HEATING_CURVE_SETTLE_SECONDS * 2 - 1800.0 + index * 300.0)

    return recorded


def test_curve_advice_sample_measures_the_whole_request(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=-2.0)

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert len(recorded) == 1
    assert recorded[0].correction == pytest.approx(1.5, abs=0.05)
    assert recorded[0].shape == HeatingCurve.calculate(21.0, 5.0)


def test_curve_advice_sample_keeps_recording_through_the_pwm_off_phase(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)
    monkeypatch.setattr(type(demand_control._coordinator), "active", property(lambda self: False))

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert len(recorded) == 1


def test_curve_advice_sample_skipped_while_boiler_has_stopped_burning(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)
    monkeypatch.setattr(demand_control._heating_control, "burned_within_pwm_period", lambda now: False)

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert recorded == []


def _driving_area(demand_control, *, output: float, error: float) -> SimpleNamespace:
    heating_curve = HeatingCurve.from_config(demand_control._config)
    heating_curve.update(19.0, 5.0)

    history = TemperatureHistory()
    for index in range(7):
        history.record(error, HEATING_CURVE_SETTLE_SECONDS * 2 - 1800.0 + index * 300.0)

    room = DrivingRoom(
        pid=SimpleNamespace(output=output),
        error=error,
        heating_curve=heating_curve,
        temperature_history=history,
        target_temperature_changed_at=0.0,
    )

    return SimpleNamespace(id="demand_control.bedroom", pid=SimpleNamespace(available=True, output=output), requires_heat=True, driving_room=room)


def test_curve_advice_sample_skipped_while_no_room_drives_the_boiler(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)
    monkeypatch.setattr(type(demand_control), "requested_setpoint", property(lambda self: self.pid.output + 5.0))

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert recorded == []


def test_curve_advice_sample_measures_the_room_that_drives_the_boiler(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)
    area = _driving_area(demand_control, output=demand_control.pid.output + 5.0, error=0.1)
    monkeypatch.setattr(demand_control.areas, "items", lambda: [area])
    monkeypatch.setattr(type(demand_control), "requested_setpoint", property(lambda self: area.pid.output))

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert len(recorded) == 1
    assert recorded[0].shape == HeatingCurve.calculate(19.0, 5.0)
    assert recorded[0].correction == pytest.approx(area.pid.output - area.driving_room.heating_curve.value)


def test_curve_advice_sample_skipped_while_the_driving_room_is_off_target(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)
    area = _driving_area(demand_control, output=demand_control.pid.output + 5.0, error=0.6)
    monkeypatch.setattr(demand_control.areas, "items", lambda: [area])
    monkeypatch.setattr(type(demand_control), "requested_setpoint", property(lambda self: area.pid.output))

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert recorded == []


def test_curve_advice_sample_skipped_while_the_driving_room_reports_a_valve_alarm(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)
    area = _driving_area(demand_control, output=demand_control.pid.output + 5.0, error=0.1)
    monkeypatch.setattr(demand_control.areas, "items", lambda: [area])
    monkeypatch.setattr(type(demand_control), "requested_setpoint", property(lambda self: area.pid.output))
    monkeypatch.setattr(RoomSignals, "read", staticmethod(lambda hass, entity_id: RoomSignalReading(valve_alarm=True)))

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert recorded == []


def test_curve_advice_sample_skipped_while_no_heat_is_requested(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)
    monkeypatch.setattr(type(demand_control.pid), "output", property(lambda self: float(COLD_SETPOINT)))
    monkeypatch.setattr(type(demand_control), "requested_setpoint", property(lambda self: float(COLD_SETPOINT)))

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert recorded == []


def test_curve_advice_sample_skipped_while_the_sun_is_up(monkeypatch, hass, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 5.0})

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert recorded == []


def test_curve_advice_sample_recorded_without_a_sun_entity(monkeypatch, hass, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)
    hass.states.async_remove("sun.sun")

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert len(recorded) == 1


def test_curve_advice_sample_carries_the_current_wind(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)
    monkeypatch.setattr(type(demand_control), "current_wind", property(lambda self: WindReading(speed=42.0, entity_id="weather.home")))

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.9, 21.0))

    assert recorded[0].wind == WindReading(speed=42.0, entity_id="weather.home")


async def test_curve_advisor_wind_is_filled_in_from_the_recorder(monkeypatch, hass, demand_control):
    sample = HeatingCurveAdviceSample(shape=10.0, timestamp=timestamp() - 7200.0, correction=1.0)
    demand_control.curve_advisor._samples.append(sample)
    hourly = {WindHistory.hour(sample.timestamp): WindReading(speed=33.0, entity_id="sensor.wind")}
    monkeypatch.setattr(demand_control._wind_history, "async_hourly", AsyncMock(return_value=hourly))

    await demand_control._async_backfill_curve_advisor_wind()

    demand_control._wind_history.async_hourly.assert_awaited_once_with(hass, sample.timestamp)
    assert demand_control.curve_advisor._samples[-1].wind == WindReading(speed=33.0, entity_id="sensor.wind")


async def test_curve_advisor_wind_backfill_skips_the_recorder_when_nothing_is_missing(monkeypatch, demand_control):
    monkeypatch.setattr(demand_control._wind_history, "async_hourly", AsyncMock(return_value={}))

    await demand_control._async_backfill_curve_advisor_wind()

    demand_control._wind_history.async_hourly.assert_not_awaited()


async def test_wind_sensitivity_receives_the_advisor_days(monkeypatch, demand_control):
    days = [WindDay(date="2026-11-02", wind_speed=13.0, coefficient=2.2, sample_count=66)]
    monkeypatch.setattr(type(demand_control.curve_advisor), "advice", property(lambda self: SimpleNamespace(wind_days=days)))
    monkeypatch.setattr(demand_control._wind_history, "async_hourly", AsyncMock(return_value={}))

    await demand_control._async_backfill_curve_advisor_wind()

    assert demand_control.wind_sensitivity.days == days




def test_curve_advice_sample_skipped_while_room_is_far_from_setpoint(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=15.0, derivative=0.0)

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(19.5, 21.0))

    assert recorded == []


def test_curve_advice_sample_skipped_while_room_has_sat_above_setpoint(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=-7.0, derivative=0.0)
    demand_control._temperature_history = TemperatureHistory()

    for index in range(7):
        demand_control._temperature_history.record(-0.3, HEATING_CURVE_SETTLE_SECONDS * 2 - 1800.0 + index * 300.0)

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(21.1, 21.0))

    assert recorded == []


def test_curve_advice_sample_skipped_before_settling(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=3.0, derivative=0.0)

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS / 2, _temperature_state(20.9, 21.0))

    assert recorded == []


async def test_target_temperature_is_pushed_to_configured_thermostat(hass, climate, demand_control):
    _update_climate_config(demand_control, climate=climate, data={CONF_THERMOSTAT: "climate.wall"}, options={CONF_PUSH_SETPOINT_TO_THERMOSTAT: True})
    hass.states.async_set("climate.wall", HVACMode.HEAT, {"temperature": 19.0, "current_temperature": 19.5})
    calls = async_mock_service(hass, "climate", "set_temperature")

    await climate.async_set_target_temperature(21.0)

    assert [(call.data["entity_id"], call.data["temperature"]) for call in calls] == [("climate.wall", 21.0)]


def test_entity_state_float_reads_climate_current_temperature(hass, demand_control):
    hass.states.async_set("climate.wall", HVACMode.HEAT, {"current_temperature": 19.5})
    hass.states.async_set("sensor.broken", "not-a-number")

    assert demand_control._get_entity_state_float("climate.wall") == 19.5
    assert demand_control._get_entity_state_float("sensor.broken") is None


def test_curve_advice_sample_skipped_while_room_is_still_warming(monkeypatch, demand_control):
    recorded = _prepare_curve_advice(monkeypatch, demand_control, proportional=10.0, derivative=0.0)
    demand_control._temperature_history = TemperatureHistory()

    for index in range(7):
        demand_control._temperature_history.record(0.9 - index * 0.1, HEATING_CURVE_SETTLE_SECONDS * 2 - 1800.0 + index * 300.0)

    demand_control._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.3, 21.0))

    assert recorded == []


def test_schedule_heating_control_loop_runs_on_the_event_loop(demand_control):
    assert is_callback(demand_control.schedule_heating_control_loop)


async def test_pending_scheduled_run_is_not_duplicated(monkeypatch, hass, demand_control):
    update_mock = AsyncMock()
    monkeypatch.setattr(demand_control._heating_control, "update", update_mock)
    demand_control._cancel_scheduled_heating_control_loop()
    demand_control._control_heating_loop_unsub = lambda: None
    demand_control._control_heating_loop_scheduled_at = timestamp()

    demand_control.schedule_heating_control_loop()
    await hass.async_block_till_done()

    update_mock.assert_not_called()


async def test_overdue_scheduled_run_is_replaced(monkeypatch, hass, demand_control):
    hass.states.async_set("sensor.test_outside_sensor", "5")
    update_mock = AsyncMock()
    monkeypatch.setattr(demand_control._heating_control, "update", update_mock)
    demand_control._cancel_scheduled_heating_control_loop()
    demand_control._control_heating_loop_unsub = lambda: None
    demand_control._control_heating_loop_scheduled_at = timestamp() - HEATING_CONTROL_SCHEDULE_TIMEOUT_SECONDS - 1

    demand_control.schedule_heating_control_loop()
    await hass.async_block_till_done()

    update_mock.assert_called_once()
    assert demand_control._control_heating_loop_unsub is None
    assert demand_control._control_heating_loop_scheduled_at is None


async def test_climate_reports_its_temperatures_natively(climate):
    climate.hass.states.async_set("sensor.test_inside_sensor", "20.4")
    await climate.async_set_target_temperature(21.0)

    assert climate.native_temperature_unit == climate.temperature_unit == climate.hass.config.units.temperature_unit
    assert climate.native_current_temperature == climate.current_temperature == 20.4
    assert climate.native_target_temperature == climate.target_temperature == 21.0


def test_climate_provides_the_native_temperature_api_beside_the_legacy_one(climate):
    for name in ("temperature_unit", "current_temperature", "target_temperature"):
        assert {name, f"native_{name}"} <= set(vars(SatClimate))
