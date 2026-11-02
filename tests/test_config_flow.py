from types import SimpleNamespace

from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.const import CONF_INSIDE_SENSOR_ENTITY_ID, CONF_OUTSIDE_SENSOR_ENTITY_ID, CONF_WIND_SENSOR_ENTITY_ID, OPTIONS_DEFAULTS
from custom_components.sat.entry_data import SatConfig, SatMode
from tests.const import DEFAULT_USER_DATA, make_config

SENSORS = {CONF_INSIDE_SENSOR_ENTITY_ID: "sensor.inside", CONF_OUTSIDE_SENSOR_ENTITY_ID: ["weather.home"]}


def _sensors_flow(hass) -> SatFlowHandler:
    flow_handler = SatFlowHandler()
    flow_handler.hass = hass
    flow_handler.config_entry = SimpleNamespace(entry_id="existing")
    flow_handler.data = {**DEFAULT_USER_DATA}

    return flow_handler


async def test_create_coordinator(hass):
    flow_handler = SatFlowHandler()
    flow_handler.data = {
        "name": "Test",
        "mode": SatMode.FAKE,
        "device": "test_device",
    }

    await flow_handler.async_create_coordinator()


async def test_sensors_step_stores_the_wind_sensors(hass):
    flow_handler = _sensors_flow(hass)

    await flow_handler.async_step_sensors({**SENSORS, CONF_WIND_SENSOR_ENTITY_ID: ["sensor.wind_speed", "weather.home"]})

    assert flow_handler.data[CONF_WIND_SENSOR_ENTITY_ID] == ["sensor.wind_speed", "weather.home"]


async def test_sensors_step_clears_removed_wind_sensors(hass):
    flow_handler = _sensors_flow(hass)
    flow_handler.data[CONF_WIND_SENSOR_ENTITY_ID] = ["sensor.wind_speed"]

    await flow_handler.async_step_sensors(SENSORS)

    assert flow_handler.data[CONF_WIND_SENSOR_ENTITY_ID] == []
    assert SatConfig(entry_id="flow", data=flow_handler.data, options=OPTIONS_DEFAULTS).sensors.wind_sensor_entity_id == []


def test_existing_entries_without_wind_sensors_have_none():
    assert make_config().sensors.wind_sensor_entity_id == []
