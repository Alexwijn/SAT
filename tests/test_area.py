from datetime import timedelta
from types import SimpleNamespace

import pytest

from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util

from custom_components.sat.area import Area, Areas, RoomDemand, ATTR_SENSOR_TEMPERATURE_ID
from custom_components.sat.const import CONF_HEATING_SYSTEM, CONF_SENSOR_MAX_VALUE_AGE, OPTIONS_DEFAULTS
from custom_components.sat.types import HeatingSystem
from tests.const import make_config


async def test_area_current_temperature_stale_climate_state(hass, monkeypatch):
    now = dt_util.utcnow()
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    hass.states.async_set("climate.room1", "heat", {"current_temperature": 21.0})

    config_options = dict(OPTIONS_DEFAULTS)
    config_options[CONF_SENSOR_MAX_VALUE_AGE] = "00:01:00"
    config = make_config(data={CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, options=config_options)

    area = Area(config, "climate.room1")
    await area.async_added_to_hass(hass, "device.test")

    monkeypatch.setattr(dt_util, "utcnow", lambda: now + timedelta(seconds=120))

    assert area.current_temperature is None


async def test_area_that_keeps_reporting_the_same_temperature_stays_fresh(hass, freezer):
    hass.states.async_set("climate.room1", "heat", {"current_temperature": 21.0})

    config_options = dict(OPTIONS_DEFAULTS)
    config_options[CONF_SENSOR_MAX_VALUE_AGE] = "00:01:00"
    config = make_config(data={CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, options=config_options)

    area = Area(config, "climate.room1")
    await area.async_added_to_hass(hass, "device.test")

    freezer.tick(timedelta(seconds=50))
    hass.states.async_set("climate.room1", "heat", {"current_temperature": 21.0})
    freezer.tick(timedelta(seconds=50))

    assert area.current_temperature == 21.0


async def test_area_off_climate_is_excluded(hass):
    hass.states.async_set("climate.room1", HVACMode.OFF, {"temperature": 21.0, "current_temperature": 18.0})

    config = make_config(data={CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, options=dict(OPTIONS_DEFAULTS))

    area = Area(config, "climate.room1")
    await area.async_added_to_hass(hass, "device.test")

    assert area.climate_state is None
    assert area.target_temperature is None
    assert area.current_temperature is None
    assert area.temperature_state is None
    assert area.requires_heat is False


async def test_area_heat_climate_is_included(hass):
    hass.states.async_set("climate.room1", HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 18.0})

    config = make_config(data={CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, options=dict(OPTIONS_DEFAULTS))

    area = Area(config, "climate.room1")
    await area.async_added_to_hass(hass, "device.test")

    assert area.climate_state is not None
    assert area.target_temperature == 21.0
    assert area.current_temperature == 18.0


async def test_area_current_temperature_stale_override_sensor(hass, monkeypatch):
    now = dt_util.utcnow()
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    hass.states.async_set(
        "climate.room1",
        "heat",
        {
            "current_temperature": 21.0,
            ATTR_SENSOR_TEMPERATURE_ID: "sensor.room1_temp",
        },
    )
    hass.states.async_set("sensor.room1_temp", "20.5")

    config_options = dict(OPTIONS_DEFAULTS)
    config_options[CONF_SENSOR_MAX_VALUE_AGE] = "00:01:00"
    config = make_config(data={CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, options=config_options)

    area = Area(config, "climate.room1")
    await area.async_added_to_hass(hass, "device.test")

    monkeypatch.setattr(dt_util, "utcnow", lambda: now + timedelta(seconds=120))

    assert area.current_temperature is None


async def test_area_stale_override_sensor_falls_back_to_the_climate(hass, freezer):
    attributes = {"current_temperature": 21.0, ATTR_SENSOR_TEMPERATURE_ID: "sensor.room1_temp"}
    hass.states.async_set("climate.room1", "heat", attributes)
    hass.states.async_set("sensor.room1_temp", "20.5")

    config = make_config(data={CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, options={**OPTIONS_DEFAULTS, CONF_SENSOR_MAX_VALUE_AGE: "00:01:00"})

    area = Area(config, "climate.room1")
    await area.async_added_to_hass(hass, "device.test")

    freezer.tick(timedelta(seconds=120))
    hass.states.async_set("climate.room1", "heat", attributes)

    assert area.current_temperature == 21.0


async def test_area_remembers_when_its_target_changed(hass):
    hass.states.async_set("climate.room1", HVACMode.HEAT, {"temperature": 18.0, "current_temperature": 17.5})

    config = make_config(data={CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, options=dict(OPTIONS_DEFAULTS))

    area = Area(config, "climate.room1")
    await area.async_added_to_hass(hass, "device.test")

    area.control_pid(dt_util.utc_from_timestamp(1000.0))
    area.control_pid(dt_util.utc_from_timestamp(1060.0))
    assert area.driving_room.target_temperature_changed_at == 1000.0
    assert area.driving_room.error == 0.5

    hass.states.async_set("climate.room1", HVACMode.HEAT, {"temperature": 20.0, "current_temperature": 17.5})
    area.control_pid(dt_util.utc_from_timestamp(1120.0))
    assert area.driving_room.target_temperature_changed_at == 1120.0


def test_room_demand_is_the_highest_request_under_the_cap():
    assert RoomDemand(request=45.0).including(40.0).setpoint == 45.0
    assert RoomDemand(request=40.0).including(45.0).setpoint == 45.0
    assert RoomDemand(request=45.0, overshoot_cap=30.0).including(40.0).setpoint == 30.0


def _room(output, error):
    return SimpleNamespace(id="climate.room", requires_heat=True, temperature_state=SimpleNamespace(error=error), pid=SimpleNamespace(available=True, output=output))


@pytest.mark.parametrize(
    ("rooms", "setpoint"),
    [
        ([_room(35.0, -0.5)], 30.0),
        ([_room(35.0, -0.5), _room(28.0, 0.5)], 30.0),
        ([_room(35.0, -0.5), _room(45.0, 1.0)], 45.0),
        ([_room(35.0, -3.0), _room(40.0, 1.0)], 40.0),
        ([_room(45.0, -0.5), _room(40.0, 0.05)], 30.0),
    ],
)
def test_overshooting_room_never_caps_below_what_colder_rooms_need(rooms, setpoint):
    assert Areas(rooms).pids.demand.setpoint == setpoint


def test_cap_never_drops_below_what_a_cold_main_room_needs():
    demand = RoomDemand(request=35.0, overshoot_cap=30.0)

    assert demand.including(42.0, below_target=True).setpoint == 42.0
    assert demand.including(42.0, below_target=False).setpoint == 30.0


@pytest.mark.parametrize(("blocked_by", "blocked"), [(None, False), ("", False), ("devices_released", True), ("valve_maintenance", True)])
async def test_blocked_room_neither_calls_nor_requires_heat(hass, blocked_by, blocked):
    hass.states.async_set("climate.room1", HVACMode.HEAT, {
        "temperature": 21.0,
        "current_temperature": 18.0,
        "hvac_action": "heating",
        "current_valve_position": 80,
        "heating_blocked_by": blocked_by,
    })

    area = Area(make_config(data={CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, options=dict(OPTIONS_DEFAULTS)), "climate.room1")
    await area.async_added_to_hass(hass, "device.test")

    assert area.is_blocked is blocked
    assert area.is_calling is not blocked
    assert area.requires_heat is not blocked
