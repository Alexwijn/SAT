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
from custom_components.sat.heating_curve import HeatingCurve
from custom_components.sat.helpers import timestamp
from custom_components.sat.temperature.history import TemperatureHistory
from custom_components.sat.temperature.state import TemperatureState
from custom_components.sat.manufacturer import ManufacturerFactory
from custom_components.sat.pid import PID
from custom_components.sat.types import HeaterState
from custom_components.sat.solar_gain import SolarGainController, SolarGainSnapshot

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


def _update_climate_config(climate, *, data=None, options=None) -> None:
    current_data = dict(climate._config.data)
    current_options = dict(climate._config.options)

    if data:
        current_data.update(data)

    if options:
        current_options.update(options)

    new_config = SatConfig(climate._config.entry_id, current_data, {**OPTIONS_DEFAULTS, **current_options})
    climate._config = new_config
    climate._coordinator._config = new_config
    climate._coordinator._manufacturer = ManufacturerFactory.resolve_by_name(new_config.manufacturer) if new_config.manufacturer else None
    climate._solar_gain = SolarGainController(new_config.solar_gain)


def test_requested_setpoint_without_heating_curve(climate):
    assert climate.heating_curve.value is None
    assert climate.requested_setpoint == MINIMUM_SETPOINT


def test_requested_setpoint_eco_mode_ignores_secondary_and_caps(monkeypatch, climate):
    _update_climate_config(climate, options={CONF_HEATING_MODE: HeatingMode.ECO})
    climate.heating_curve._value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 42.44))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 55.0))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 35.0))

    assert climate.requested_setpoint == 42.4


def test_requested_setpoint_uses_secondary_and_cap(monkeypatch, climate):
    _update_climate_config(climate, options={CONF_HEATING_MODE: HeatingMode.COMFORT})
    climate.heating_curve._value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 40.2))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 45.6))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 43.1))

    assert climate.requested_setpoint == 43.1


def test_requested_setpoint_applies_solar_gain_offset(monkeypatch, climate):
    _update_climate_config(climate, options={
        CONF_SOLAR_GAIN_COMPENSATION: True,
        CONF_SOLAR_GAIN_SETPOINT_OFFSET_CELSIUS: 2.0,
    })
    climate.heating_curve._value = 30.0
    climate._solar_gain_snapshot = SolarGainSnapshot(active=True, rise_per_hour=0.8, sun_elevation=20.0)

    monkeypatch.setattr(PID, "output", property(lambda self: 41.2))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: None))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: None))

    assert climate.requested_setpoint == 39.2


def test_update_heating_curves_updates_value(climate):
    climate.hass.states.async_set("sensor.test_inside_sensor", "20")
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")
    climate._target_temperature = 21.0

    climate._update_heating_curves()

    base_offset = HeatingSystem.RADIATORS.base_offset
    coefficient = float(OPTIONS_DEFAULTS[CONF_HEATING_CURVE_COEFFICIENT])
    expected_curve = HeatingCurve.calculate(21.0, 5.0)
    expected_value = round(base_offset + ((coefficient / 4) * expected_curve), 1)

    assert climate.heating_curve.value == expected_value


async def test_set_target_temperature_updates_heating_curve(climate):
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")

    await climate.async_set_target_temperature(21.0, cascade=False)

    base_offset = HeatingSystem.RADIATORS.base_offset
    coefficient = float(OPTIONS_DEFAULTS[CONF_HEATING_CURVE_COEFFICIENT])
    expected_curve = HeatingCurve.calculate(21.0, 5.0)
    expected_value = round(base_offset + ((coefficient / 4) * expected_curve), 1)

    assert climate.heating_curve.value == expected_value


async def test_area_climate_change_updates_heating_curve(climate):
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")
    climate._target_temperature = 21.0
    climate._rooms = {"climate.room": 20.0}

    new_state = State("climate.room", HVACMode.HEAT, {"temperature": 22.0})
    event = SimpleNamespace(data={"new_state": new_state})

    await climate._async_climate_changed(event)

    base_offset = HeatingSystem.RADIATORS.base_offset
    coefficient = float(OPTIONS_DEFAULTS[CONF_HEATING_CURVE_COEFFICIENT])
    expected_curve = HeatingCurve.calculate(21.0, 5.0)
    expected_value = round(base_offset + ((coefficient / 4) * expected_curve), 1)

    assert climate.heating_curve.value == expected_value


def test_control_pid_resets_on_stale_inside_sensor(monkeypatch, climate):
    _update_climate_config(climate, options={CONF_SENSOR_MAX_VALUE_AGE: "00:01:00"})
    climate._target_temperature = 21.0

    now = dt_util.utcnow()
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    climate.hass.states.async_set("sensor.test_inside_sensor", "20")
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")

    monkeypatch.setattr(dt_util, "utcnow", lambda: now + timedelta(seconds=120))

    reset_called = {"value": False}

    def _reset(self):
        reset_called["value"] = True

    monkeypatch.setattr(PID, "reset", _reset)

    climate.control_pid()

    assert reset_called["value"] is True


async def test_control_loop_passes_the_target_temperature_change_time(monkeypatch, climate):
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")
    climate._hvac_mode = HVACMode.HEAT

    update_mock = AsyncMock()
    monkeypatch.setattr(climate._heating_control, "update", update_mock)

    await climate.async_set_target_temperature(21.0, cascade=False)
    await climate.async_control_heating_loop()

    demand = update_mock.call_args.args[0]
    assert demand.target_temperature_changed_at == climate._target_temperature_changed_at
    assert demand.target_temperature_changed_at is not None


async def test_control_loop_commands_heater_off_when_hvac_off(monkeypatch, climate):
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")
    climate._hvac_mode = HVACMode.OFF

    update_mock = AsyncMock()
    monkeypatch.setattr(climate._heating_control, "update", update_mock)

    await climate.async_control_heating_loop()

    update_mock.assert_called_once()

    demand = update_mock.call_args.args[0]
    assert demand.hvac_mode == HVACMode.OFF
    assert demand.requested_setpoint == MINIMUM_SETPOINT
    assert demand.heater_state == HeaterState.OFF


def test_register_event_listeners_is_idempotent(monkeypatch, climate):
    climate._event_listeners_registered = False
    calls = {"time_interval": 0, "state_change": 0}

    def _track_time_interval(*args, **kwargs):
        calls["time_interval"] += 1
        return lambda: None

    def _track_state_change_event(*args, **kwargs):
        calls["state_change"] += 1
        return lambda: None

    monkeypatch.setattr("custom_components.sat.climate.async_track_time_interval", _track_time_interval)
    monkeypatch.setattr("custom_components.sat.climate.async_track_state_change_event", _track_state_change_event)

    climate._register_event_listeners()
    first_counts = dict(calls)
    climate._register_event_listeners()

    assert calls == first_counts


def test_control_pid_freezes_integral_when_solar_gain_detected(monkeypatch, climate):
    _update_climate_config(climate, options={
        CONF_SOLAR_GAIN_COMPENSATION: True,
        CONF_SOLAR_GAIN_FREEZE_INTEGRAL: True,
        CONF_SOLAR_GAIN_MIN_ELEVATION: 10.0,
        CONF_SOLAR_GAIN_MIN_RISE_PER_HOUR: 0.5,
    })

    now = dt_util.utcnow()
    climate._target_temperature = 21.0
    climate._hvac_mode = HVACMode.HEAT
    climate.hass.states.async_set("sensor.test_inside_sensor", "20.0")
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")
    climate.hass.states.async_set("sun.sun", "above_horizon", {"elevation": 20.0})
    climate._coordinator._relative_modulation_value = 0

    calls: list[bool] = []

    def _update(self, state, freeze_integral=False):
        calls.append(freeze_integral)

    monkeypatch.setattr(PID, "update", _update)
    climate.control_pid(now)
    climate.hass.states.async_set("sensor.test_inside_sensor", "20.3")
    climate.control_pid(now + timedelta(minutes=15))

    assert calls[-1] is True


async def test_reset_integral_entity_service(hass, climate):
    climate.pid._integral = 5.0

    await hass.services.async_call(DOMAIN, SERVICE_RESET_INTEGRAL, {"entity_id": climate.entity_id}, blocking=True)

    assert climate.pid.integral == 0.0


async def test_pulse_width_modulation_entity_service(hass, climate):
    await hass.services.async_call(DOMAIN, SERVICE_PULSE_WIDTH_MODULATION, {"entity_id": climate.entity_id, "enabled": True}, blocking=True)
    assert climate._heating_control.pwm_state.enabled is True

    await hass.services.async_call(DOMAIN, SERVICE_PULSE_WIDTH_MODULATION, {"entity_id": climate.entity_id, "enabled": False}, blocking=True)
    assert climate._heating_control.pwm_state.enabled is False


async def test_frost_protection_heats_when_off(monkeypatch, climate):
    climate.hass.states.async_set("sensor.test_inside_sensor", "4.0")
    climate.hass.states.async_set("sensor.test_outside_sensor", "-5")
    climate._hvac_mode = HVACMode.OFF

    update_mock = AsyncMock()
    monkeypatch.setattr(climate._heating_control, "update", update_mock)

    await climate.async_control_heating_loop()

    demand = update_mock.call_args.args[0]
    assert climate._frost_protection_active is True
    assert demand.hvac_mode == HVACMode.HEAT
    assert demand.heater_state == HeaterState.ON


async def test_frost_protection_stays_off_when_warm(monkeypatch, climate):
    climate.hass.states.async_set("sensor.test_inside_sensor", "18.0")
    climate.hass.states.async_set("sensor.test_outside_sensor", "-5")
    climate._hvac_mode = HVACMode.OFF

    update_mock = AsyncMock()
    monkeypatch.setattr(climate._heating_control, "update", update_mock)

    await climate.async_control_heating_loop()

    demand = update_mock.call_args.args[0]
    assert climate._frost_protection_active is False
    assert demand.heater_state == HeaterState.OFF


def _temperature_state(current: float, setpoint: float) -> TemperatureState:
    now = dt_util.utcnow()
    return TemperatureState(entity_id="sensor.test_inside_sensor", current=current, setpoint=setpoint, last_updated=now, last_changed=now, last_reported=now)


def _prepare_curve_advice(monkeypatch, climate, *, proportional: float, derivative: float) -> list:
    recorded: list = []

    climate._hvac_mode = HVACMode.HEAT
    climate._target_temperature_changed_at = 0.0
    climate.heating_curve.update(21.0, 5.0)

    monkeypatch.setattr(type(climate.pid), "proportional", property(lambda self: proportional))
    monkeypatch.setattr(type(climate.pid), "integral", property(lambda self: 0.5))
    monkeypatch.setattr(type(climate.pid), "derivative", property(lambda self: derivative))
    monkeypatch.setattr(type(climate._coordinator), "active", property(lambda self: True))
    monkeypatch.setattr(climate._curve_advisor, "record", recorded.append)

    for index in range(7):
        climate._temperature_history.record(0.3, HEATING_CURVE_SETTLE_SECONDS * 2 - 1800.0 + index * 300.0)

    return recorded


def test_curve_advice_sample_excludes_derivative(monkeypatch, climate):
    recorded = _prepare_curve_advice(monkeypatch, climate, proportional=3.0, derivative=-2.0)

    climate._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.7, 21.0))

    assert len(recorded) == 1
    assert recorded[0].correction == 3.5
    assert recorded[0].shape == HeatingCurve.calculate(21.0, 5.0)


def test_implied_coefficient_follows_the_steady_sample(monkeypatch, climate):
    _prepare_curve_advice(monkeypatch, climate, proportional=3.0, derivative=0.0)
    shape = HeatingCurve.calculate(21.0, 5.0)

    climate._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.7, 21.0))

    assert climate.implied_curve_coefficient == round(climate._config.pid.heating_curve_coefficient + 4 * 3.5 / shape, 2)

    monkeypatch.setattr(type(climate._coordinator), "active", property(lambda self: False))
    climate._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.7, 21.0))

    assert climate.implied_curve_coefficient is None


def test_implied_coefficient_clears_during_hot_water_and_solar_gain(monkeypatch, climate):
    _prepare_curve_advice(monkeypatch, climate, proportional=3.0, derivative=0.0)
    now, state = HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.7, 21.0)

    climate._record_curve_advice_sample(now, state)
    assert climate.implied_curve_coefficient is not None

    monkeypatch.setattr(type(climate._coordinator), "hot_water_active", property(lambda self: True))
    climate._record_curve_advice_sample(now, state)
    assert climate.implied_curve_coefficient is None

    monkeypatch.setattr(type(climate._coordinator), "hot_water_active", property(lambda self: False))
    monkeypatch.setattr(climate, "_solar_gain_snapshot", SimpleNamespace(active=True))
    climate._record_curve_advice_sample(now, state)
    assert climate.implied_curve_coefficient is None


def test_implied_coefficient_clears_when_the_control_loop_cannot_run(monkeypatch, climate):
    _prepare_curve_advice(monkeypatch, climate, proportional=3.0, derivative=0.0)
    climate._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.7, 21.0))
    assert climate.implied_curve_coefficient is not None

    monkeypatch.setattr(type(climate), "temperature_state", property(lambda self: None))
    climate.control_pid()

    assert climate.implied_curve_coefficient is None


def test_curve_advice_sample_skipped_while_boiler_is_idle(monkeypatch, climate):
    recorded = _prepare_curve_advice(monkeypatch, climate, proportional=3.0, derivative=0.0)
    monkeypatch.setattr(type(climate._coordinator), "active", property(lambda self: False))

    climate._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.7, 21.0))

    assert recorded == []


def test_curve_advice_sample_skipped_while_room_is_far_from_setpoint(monkeypatch, climate):
    recorded = _prepare_curve_advice(monkeypatch, climate, proportional=15.0, derivative=0.0)

    climate._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(19.5, 21.0))

    assert recorded == []


def test_curve_advice_sample_skipped_before_settling(monkeypatch, climate):
    recorded = _prepare_curve_advice(monkeypatch, climate, proportional=3.0, derivative=0.0)

    climate._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS / 2, _temperature_state(20.7, 21.0))

    assert recorded == []


async def test_target_temperature_is_pushed_to_configured_thermostat(hass, climate):
    _update_climate_config(climate, data={CONF_THERMOSTAT: "climate.wall"}, options={CONF_PUSH_SETPOINT_TO_THERMOSTAT: True})
    hass.states.async_set("climate.wall", HVACMode.HEAT, {"temperature": 19.0, "current_temperature": 19.5})
    calls = async_mock_service(hass, "climate", "set_temperature")

    await climate.async_set_target_temperature(21.0)

    assert [(call.data["entity_id"], call.data["temperature"]) for call in calls] == [("climate.wall", 21.0)]


def test_entity_state_float_reads_climate_current_temperature(climate):
    climate.hass.states.async_set("climate.wall", HVACMode.HEAT, {"current_temperature": 19.5})
    climate.hass.states.async_set("sensor.broken", "not-a-number")

    assert climate._get_entity_state_float("climate.wall") == 19.5
    assert climate._get_entity_state_float("sensor.broken") is None


def test_curve_advice_sample_skipped_while_room_is_still_warming(monkeypatch, climate):
    recorded = _prepare_curve_advice(monkeypatch, climate, proportional=10.0, derivative=0.0)
    climate._temperature_history = TemperatureHistory()

    for index in range(7):
        climate._temperature_history.record(0.9 - index * 0.1, HEATING_CURVE_SETTLE_SECONDS * 2 - 1800.0 + index * 300.0)

    climate._record_curve_advice_sample(HEATING_CURVE_SETTLE_SECONDS * 2, _temperature_state(20.3, 21.0))

    assert recorded == []


def test_schedule_heating_control_loop_runs_on_the_event_loop(climate):
    assert is_callback(climate.schedule_heating_control_loop)


async def test_pending_scheduled_run_is_not_duplicated(monkeypatch, climate):
    update_mock = AsyncMock()
    monkeypatch.setattr(climate._heating_control, "update", update_mock)
    climate._control_heating_loop_unsub = lambda: None
    climate._control_heating_loop_scheduled_at = timestamp()

    climate.schedule_heating_control_loop()
    await climate.hass.async_block_till_done()

    update_mock.assert_not_called()


async def test_overdue_scheduled_run_is_replaced(monkeypatch, climate):
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")
    update_mock = AsyncMock()
    monkeypatch.setattr(climate._heating_control, "update", update_mock)
    climate._control_heating_loop_unsub = lambda: None
    climate._control_heating_loop_scheduled_at = timestamp() - HEATING_CONTROL_SCHEDULE_TIMEOUT_SECONDS - 1

    climate.schedule_heating_control_loop()
    await climate.hass.async_block_till_done()

    update_mock.assert_called_once()
    assert climate._control_heating_loop_unsub is None
    assert climate._control_heating_loop_scheduled_at is None
