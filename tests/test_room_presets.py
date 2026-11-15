"""Tests for presets applied per room."""

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode, PRESET_AWAY, PRESET_COMFORT, PRESET_HOME, PRESET_NONE, PRESET_SLEEP
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sat.config_flow import SatOptionsFlowHandler
from custom_components.sat.const import (
    CONF_AWAY_TEMPERATURE,
    CONF_COMFORT_TEMPERATURE,
    CONF_HEATING_SYSTEM,
    CONF_HOME_TEMPERATURE,
    CONF_INSIDE_SENSOR_ENTITY_ID,
    CONF_ROOM,
    CONF_ROOM_PRESET_FIXED,
    CONF_ROOM_PRESET_TEMPERATURES,
    CONF_ROOM_PRESETS,
    CONF_ROOMS,
    CONF_SLEEP_TEMPERATURE,
    CONF_SYNC_CLIMATES_WITH_PRESET,
)
from custom_components.sat.presets import RoomPresets
from custom_components.sat.types import HeatingSystem
from tests.const import make_config

KITCHEN = "climate.kitchen"
BEDROOM = "climate.bedroom"
COUCH = "climate.couch"
NURSERY = "climate.nursery"

HOUSE = {CONF_HOME_TEMPERATURE: 21, CONF_SLEEP_TEMPERATURE: 19, CONF_AWAY_TEMPERATURE: 16, CONF_COMFORT_TEMPERATURE: 23}
ROOM_PRESETS = {
    COUCH: {CONF_ROOM_PRESET_FIXED: False, CONF_ROOM_PRESET_TEMPERATURES: {PRESET_COMFORT: 23.0}},
    NURSERY: {CONF_ROOM_PRESET_FIXED: True, CONF_ROOM_PRESET_TEMPERATURES: {}},
}

WITH_MAIN_ROOM = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [([], {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS, CONF_ROOMS: [KITCHEN, BEDROOM, COUCH, NURSERY]}, {**HOUSE, CONF_SYNC_CLIMATES_WITH_PRESET: True, CONF_ROOM_PRESETS: ROOM_PRESETS}, {})],
)

ROOMS_ONLY = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [([], {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS, CONF_INSIDE_SENSOR_ENTITY_ID: None, CONF_ROOMS: [KITCHEN, BEDROOM, COUCH, NURSERY]}, {**HOUSE, CONF_ROOM_PRESETS: ROOM_PRESETS}, {})],
)


@pytest.fixture
async def room_states(hass):
    hass.states.async_set(KITCHEN, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 20.5})
    hass.states.async_set(BEDROOM, HVACMode.HEAT, {"temperature": 18.0, "current_temperature": 17.5})
    hass.states.async_set(COUCH, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 20.0})
    hass.states.async_set(NURSERY, HVACMode.HEAT, {"temperature": 20.0, "current_temperature": 19.5})
    hass.states.async_set("sensor.test_inside_sensor", "20.0")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")


def _presets() -> RoomPresets:
    return RoomPresets(make_config(options={**HOUSE, CONF_ROOM_PRESETS: ROOM_PRESETS}).presets)


def _set_temperature_calls(calls) -> dict[str, float]:
    return {call.data["entity_id"]: call.data["temperature"] for call in calls}


def test_setbacks_lower_a_room_relative_to_its_home_target():
    presets = _presets()

    assert presets.target(KITCHEN, PRESET_SLEEP, 21.0) == 19.0
    assert presets.target(KITCHEN, PRESET_AWAY, 21.0) == 16.0
    assert presets.target(BEDROOM, PRESET_SLEEP, 18.0) == 18.0
    assert presets.target(BEDROOM, PRESET_AWAY, 18.0) == 16.0


def test_boosts_never_raise_a_room():
    presets = _presets()

    assert presets.target(KITCHEN, PRESET_COMFORT, 21.0) == 21.0
    assert presets.target(BEDROOM, PRESET_COMFORT, 18.0) == 18.0


def test_a_room_with_its_own_value_follows_it():
    presets = _presets()

    assert presets.target(COUCH, PRESET_COMFORT, 21.0) == 23.0
    assert presets.target(COUCH, PRESET_SLEEP, 21.0) == 19.0


def test_a_fixed_room_is_left_alone():
    presets = _presets()

    for preset in (PRESET_HOME, PRESET_NONE, PRESET_SLEEP, PRESET_AWAY, PRESET_COMFORT):
        assert presets.target(NURSERY, preset, 20.0) is None


def test_home_and_no_preset_restore_the_home_target():
    presets = _presets()

    assert presets.target(KITCHEN, PRESET_HOME, 21.0) == 21.0
    assert presets.target(KITCHEN, PRESET_NONE, 21.0) == 21.0
    assert presets.target(KITCHEN, PRESET_SLEEP, None) is None


@WITH_MAIN_ROOM
async def test_sleep_lowers_the_rooms_relatively(room_states, hass, climate, domains, data, options, config):
    calls = async_mock_service(hass, "climate", "set_temperature")

    await climate.async_set_preset_mode(PRESET_SLEEP)

    assert climate.target_temperature == 19.0
    assert _set_temperature_calls(calls) == {KITCHEN: 19.0, COUCH: 19.0}


@WITH_MAIN_ROOM
async def test_comfort_raises_only_the_room_that_asked_for_it(room_states, hass, climate, domains, data, options, config):
    calls = async_mock_service(hass, "climate", "set_temperature")

    await climate.async_set_preset_mode(PRESET_COMFORT)

    assert climate.target_temperature == 23.0
    assert _set_temperature_calls(calls) == {COUCH: 23.0}


@WITH_MAIN_ROOM
async def test_leaving_the_preset_restores_the_home_targets(room_states, hass, climate, domains, data, options, config):
    await climate.async_set_preset_mode(PRESET_AWAY)
    hass.states.async_set(KITCHEN, HVACMode.HEAT, {"temperature": 16.0, "current_temperature": 20.5})
    hass.states.async_set(BEDROOM, HVACMode.HEAT, {"temperature": 16.0, "current_temperature": 17.5})
    hass.states.async_set(COUCH, HVACMode.HEAT, {"temperature": 16.0, "current_temperature": 20.0})
    calls = async_mock_service(hass, "climate", "set_temperature")

    await climate.async_set_preset_mode(PRESET_NONE)

    assert _set_temperature_calls(calls) == {KITCHEN: 21.0, BEDROOM: 18.0, COUCH: 21.0}
    assert climate.extra_state_attributes["rooms"] == {KITCHEN: 21.0, BEDROOM: 18.0, COUCH: 21.0, NURSERY: 20.0}


@WITH_MAIN_ROOM
async def test_room_changes_while_away_are_not_remembered_as_home(room_states, hass, climate, domains, data, options, config):
    await climate.async_set_preset_mode(PRESET_AWAY)

    hass.states.async_set(BEDROOM, HVACMode.HEAT, {"temperature": 15.0, "current_temperature": 17.5})
    await hass.async_block_till_done()

    assert climate.extra_state_attributes["rooms"][BEDROOM] == 18.0


@ROOMS_ONLY
async def test_rooms_only_climate_offers_the_presets(room_states, hass, climate, domains, data, options, config):
    assert climate.supported_features & ClimateEntityFeature.PRESET_MODE
    assert not climate.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
    assert PRESET_SLEEP in climate.preset_modes
    assert climate.preset_mode == PRESET_NONE


@ROOMS_ONLY
async def test_rooms_only_presets_set_the_rooms(room_states, hass, climate, domains, data, options, config):
    calls = async_mock_service(hass, "climate", "set_temperature")

    await climate.async_set_preset_mode(PRESET_COMFORT)
    assert climate.preset_mode == PRESET_COMFORT
    assert climate.target_temperature is None
    assert _set_temperature_calls(calls) == {COUCH: 23.0}

    calls.clear()
    await climate.async_set_preset_mode(PRESET_AWAY)
    assert _set_temperature_calls(calls) == {KITCHEN: 16.0, BEDROOM: 16.0, COUCH: 16.0}


@ROOMS_ONLY
async def test_options_flow_stores_the_room_presets(hass, entry, domains, data, options, config):
    flow = SatOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_room_presets({CONF_ROOM: BEDROOM})
    assert result["step_id"] == "room_preset"

    result = await flow.async_step_room_preset({CONF_ROOM_PRESET_FIXED: False, CONF_COMFORT_TEMPERATURE: 18.0})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ROOM_PRESETS][BEDROOM] == {CONF_ROOM_PRESET_FIXED: False, CONF_ROOM_PRESET_TEMPERATURES: {PRESET_COMFORT: 18.0}}
    assert result["data"][CONF_ROOM_PRESETS][COUCH] == ROOM_PRESETS[COUCH]


@ROOMS_ONLY
async def test_options_menu_offers_room_presets(hass, entry, domains, data, options, config):
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert "presets" in result["menu_options"]
    assert "room_presets" in result["menu_options"]
