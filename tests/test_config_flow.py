import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.const import (
    CONF_AUTOMATIC_GAINS,
    CONF_HEATING_SYSTEM,
    CONF_INSIDE_SENSOR_ENTITY_ID,
    CONF_NAME,
    CONF_OUTSIDE_SENSOR_ENTITY_ID,
    CONF_OVERSHOOT_PROTECTION,
    CONF_WIND_SENSOR_ENTITY_ID,
    DOMAIN,
    OPTIONS_DEFAULTS,
)
from custom_components.sat.entry_data import SatConfig, SatMode
from custom_components.sat.types import HeatingSystem
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


async def test_new_entry_enables_overshoot_protection(hass):
    flow = hass.config_entries.flow

    with patch("custom_components.sat.async_setup_entry", return_value=True):
        result = await flow.async_init(DOMAIN, context={"source": SOURCE_USER, "show_advanced_options": True})
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "simulator"})
        result = await flow.async_configure(result["flow_id"], {CONF_NAME: "Test"})
        result = await flow.async_configure(result["flow_id"], SENSORS)
        result = await flow.async_configure(result["flow_id"], {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS})
        result = await flow.async_configure(result["flow_id"], {})
        result = await flow.async_configure(result["flow_id"], {CONF_AUTOMATIC_GAINS: True})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_OVERSHOOT_PROTECTION not in result["data"]
    assert SatConfig(entry_id="flow", data=result["data"], options=OPTIONS_DEFAULTS).overshoot_protection is True


def test_entries_that_disabled_overshoot_protection_keep_it_disabled():
    assert make_config(data={CONF_OVERSHOOT_PROTECTION: False}).overshoot_protection is False


async def test_manufacturer_step_does_not_release_the_boiler(hass):
    flow_handler = _sensors_flow(hass)

    with patch("custom_components.sat.coordinator.SatDataUpdateCoordinator.async_release") as release:
        await flow_handler.async_step_manufacturer()

    release.assert_not_called()


async def test_system_configuration_offers_the_window_close_delay(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA, options={})
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "system_configuration"})

    defaults = {str(key): key.default() for key in result["data_schema"].schema}

    assert defaults["window_minimum_closed_time"] == "00:00:00"


def test_existing_entries_have_no_window_close_delay():
    assert make_config().sensors.window_minimum_closed_time_seconds == 0


def test_window_close_delay_is_translated_in_every_language():
    for file in sorted((Path(__file__).parent.parent / "custom_components" / "sat" / "translations").glob("*.json")):
        step = json.loads(file.read_text())["options"]["step"]["system_configuration"]

        assert step["data"]["window_minimum_closed_time"], file.stem
        assert step["data_description"]["window_minimum_closed_time"], file.stem
