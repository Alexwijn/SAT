"""Tests for running SAT from the rooms alone, without a primary room."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.const import CONF_HEATING_SYSTEM, CONF_INSIDE_SENSOR_ENTITY_ID, CONF_OUTSIDE_SENSOR_ENTITY_ID, CONF_ROOMS, DOMAIN
from custom_components.sat.room_balance import MAIN_ROOM_ID
from custom_components.sat.types import HeatingSystem
from tests.const import DEFAULT_USER_DATA, make_config

ROOM = "climate.bedroom"
OTHER_ROOM = "climate.kitchen"

ROOMS_ONLY = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [([], {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS, CONF_INSIDE_SENSOR_ENTITY_ID: None, CONF_ROOMS: [ROOM, OTHER_ROOM]}, {}, {})],
)


@pytest.fixture
async def room_states(hass):
    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 19.0, "hvac_action": HVACAction.HEATING})
    hass.states.async_set(OTHER_ROOM, HVACMode.HEAT, {"temperature": 20.0, "current_temperature": 20.0, "hvac_action": HVACAction.IDLE})
    hass.states.async_set("sensor.test_outside_sensor", "5.0")


def _flow(hass) -> SatFlowHandler:
    flow_handler = SatFlowHandler()
    flow_handler.hass = hass
    flow_handler.config_entry = SimpleNamespace(entry_id="existing")
    flow_handler.data = {**DEFAULT_USER_DATA}

    return flow_handler


def test_config_without_inside_sensor_is_rooms_only():
    assert make_config().rooms_only is False

    config = make_config(data={CONF_INSIDE_SENSOR_ENTITY_ID: None})
    assert config.rooms_only is True
    assert config.sensors.inside_sensor_entity_id is None


async def test_sensors_step_stores_a_missing_inside_sensor_as_none(hass):
    flow_handler = _flow(hass)

    await flow_handler.async_step_sensors({CONF_OUTSIDE_SENSOR_ENTITY_ID: ["weather.home"]})

    assert flow_handler.data[CONF_INSIDE_SENSOR_ENTITY_ID] is None


async def test_areas_step_requires_rooms_without_an_inside_sensor(hass):
    flow_handler = _flow(hass)
    flow_handler.data[CONF_INSIDE_SENSOR_ENTITY_ID] = None

    result = await flow_handler.async_step_areas({})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "areas"
    assert result["errors"] == {CONF_ROOMS: "rooms_required"}

    result = await flow_handler.async_step_areas({CONF_ROOMS: [ROOM]})

    assert result["step_id"] == "automatic_gains"


async def test_areas_step_allows_no_rooms_with_an_inside_sensor(hass):
    flow_handler = _flow(hass)

    result = await flow_handler.async_step_areas({})

    assert result["step_id"] == "automatic_gains"


@ROOMS_ONLY
async def test_options_menu_has_no_presets(hass, entry, domains, data, options, config):
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert "presets" not in result["menu_options"]
    assert "general" in result["menu_options"]


@ROOMS_ONLY
async def test_no_climate_entity_and_no_main_room_sensors(hass, entry, demand_control, domains, data, options, config):
    registry = er.async_get(hass)

    assert hass.data[DOMAIN][entry.entry_id].climate is None
    assert registry.async_get_entity_id("climate", DOMAIN, entry.entry_id) is None

    for suffix in ("pid", "error-value", "heating-curve", f"{MAIN_ROOM_ID}-room-balance"):
        assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}-{suffix}") is None

    for suffix in ("requested-setpoint", "outside-temperature", "heating-curve-recommendation", f"{ROOM}-pid", f"{ROOM}-room-balance"):
        assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}-{suffix}") is not None

    assert demand_control.id == entry.entry_id
    assert demand_control.hvac_mode == HVACMode.HEAT
    assert demand_control.temperature_state is None


@ROOMS_ONLY
async def test_requested_setpoint_follows_the_rooms(monkeypatch, demand_control, domains, data, options, config):
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 44.3))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: None))

    assert demand_control.heating_curve.value is None
    assert demand_control.requested_setpoint == 44.3

    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 40.0))

    assert demand_control.requested_setpoint == 40.0


@ROOMS_ONLY
async def test_valves_follow_the_rooms(room_states, hass, demand_control, domains, data, options, config):
    assert demand_control.valves_open is True

    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 21.0, "hvac_action": HVACAction.IDLE})
    assert demand_control.valves_open is False

    hass.states.async_set(OTHER_ROOM, HVACMode.HEAT, {"temperature": 22.0, "current_temperature": 20.0})
    assert demand_control.valves_open is True

    hass.states.async_set(OTHER_ROOM, HVACMode.OFF, {"temperature": 22.0, "current_temperature": 20.0})
    assert demand_control.valves_open is False


@ROOMS_ONLY
async def test_rooms_get_a_heating_curve_without_a_primary(room_states, hass, demand_control, domains, data, options, config):
    demand_control._update_heating_curves()

    assert demand_control.heating_curve.value is None
    assert demand_control.areas.get(ROOM).heating_curve.value is not None


@ROOMS_ONLY
async def test_control_pid_feeds_the_monitors_from_the_rooms(room_states, hass, demand_control, monkeypatch, domains, data, options, config):
    demand_control._update_heating_curves()
    demand_control.areas.control_pid()

    seen = []
    monkeypatch.setattr(demand_control.room_balance, "observe", lambda boiler, rooms, now: seen.append(rooms))
    samples_before = demand_control.temperature_statistics.window.recent.sample_count

    demand_control.control_pid()

    assert [room.room_id for room in seen[0]] == [ROOM, OTHER_ROOM]
    assert demand_control.median_error == 1.0
    assert demand_control.temperature_statistics.window.recent.sample_count == samples_before + 1


@ROOMS_ONLY
async def test_heating_loop_runs_from_the_rooms(room_states, hass, demand_control, monkeypatch, domains, data, options, config):
    update_mock = AsyncMock()
    monkeypatch.setattr(demand_control._heating_control, "update", update_mock)

    await demand_control.async_control_heating_loop()

    demand = update_mock.call_args.args[0]
    assert demand.hvac_mode == HVACMode.HEAT
    assert demand.valves_open is True
    assert demand.outside_temperature == 5.0


@ROOMS_ONLY
async def test_unload_stops_the_demand_control(hass, entry, demand_control, domains, data, options, config):
    assert demand_control._started is True

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert demand_control._started is False
