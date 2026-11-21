from datetime import timedelta

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from custom_components.sat.const import CONF_OUTSIDE_SENSOR_ENTITY_ID
from custom_components.sat.outside_temperature import OutsideTemperature
from tests.const import make_config

SENSOR = "sensor.outside"
WEATHER = "weather.home"


@pytest.fixture
def outside_temperature():
    return OutsideTemperature(make_config(data={CONF_OUTSIDE_SENSOR_ENTITY_ID: [WEATHER, SENSOR]}).sensors)


async def test_sensor_is_preferred_over_weather(hass, outside_temperature):
    hass.states.async_set(SENSOR, "4.5")
    hass.states.async_set(WEATHER, "cloudy", {"temperature": 7.0})

    assert outside_temperature.read(hass) == 4.5


@pytest.mark.parametrize("value", [STATE_UNKNOWN, STATE_UNAVAILABLE])
async def test_sensor_without_a_state_falls_back_to_weather(hass, outside_temperature, value):
    hass.states.async_set(SENSOR, value)
    hass.states.async_set(WEATHER, "cloudy", {"temperature": 7.0})

    assert outside_temperature.read(hass) == 7.0


async def test_stale_sensor_falls_back_to_weather(hass, outside_temperature, freezer):
    hass.states.async_set(SENSOR, "4.5")
    freezer.tick(timedelta(hours=7))
    hass.states.async_set(WEATHER, "cloudy", {"temperature": 7.0})

    assert outside_temperature.read(hass) == 7.0


async def test_no_source_gives_no_temperature(hass, outside_temperature):
    assert outside_temperature.read(hass) is None


@pytest.mark.parametrize("value", ["", "none", "nan"])
async def test_sensor_without_a_number_falls_back_to_weather(hass, outside_temperature, value):
    hass.states.async_set(SENSOR, value)
    hass.states.async_set(WEATHER, "cloudy", {"temperature": 7.0})

    assert outside_temperature.read(hass) == 7.0


async def test_weather_without_a_temperature_gives_no_temperature(hass, outside_temperature):
    hass.states.async_set(WEATHER, "cloudy")

    assert outside_temperature.read(hass) is None
