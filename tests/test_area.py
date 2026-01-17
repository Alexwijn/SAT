from datetime import timedelta

from homeassistant.util import dt as dt_util

from custom_components.sat.area import Area, ATTR_SENSOR_TEMPERATURE_ID
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
