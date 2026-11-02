"""Unit tests for the wind monitor."""

from datetime import timedelta

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import SpeedConverter
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.sat.const import CONF_OUTSIDE_SENSOR_ENTITY_ID, CONF_WIND_SENSOR_ENTITY_ID, DOMAIN
from custom_components.sat.helpers import timestamp
from custom_components.sat.wind import WIND_SMOOTHING_WINDOW_SECONDS, WindHistory, WindMonitor, WindReading
from tests.const import make_config

WEATHER = "weather.home"
ANEMOMETER = "sensor.garden_wind_speed"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations():
    """Replace the shared fixture, which sets up hass before recorder_mock is allowed to."""
    yield


def _monitor(wind: list[str], outside: list[str] | str = "sensor.test_outside_sensor") -> WindMonitor:
    return WindMonitor(make_config({CONF_WIND_SENSOR_ENTITY_ID: wind, CONF_OUTSIDE_SENSOR_ENTITY_ID: outside}).sensors)


def _set_weather(hass, speed: float, unit: str = "km/h") -> None:
    hass.states.async_set(WEATHER, "cloudy", {"temperature": 10.0, "wind_speed": speed, "wind_speed_unit": unit})


def _set_anemometer(hass, speed, unit: str = "m/s") -> None:
    hass.states.async_set(ANEMOMETER, speed, {ATTR_UNIT_OF_MEASUREMENT: unit})


def test_wind_sensors_are_preferred_over_weather_entities():
    config = make_config({CONF_WIND_SENSOR_ENTITY_ID: [WEATHER, ANEMOMETER]}).sensors

    assert WindMonitor.sources(config) == [ANEMOMETER, WEATHER]


def test_weather_entities_among_the_outside_sensors_are_used_without_wind_sensors():
    config = make_config({CONF_OUTSIDE_SENSOR_ENTITY_ID: ["sensor.test_outside_sensor", WEATHER]}).sensors

    assert WindMonitor.sources(config) == [WEATHER]


def test_no_wind_source_without_wind_sensors_or_weather_entities():
    assert WindMonitor.sources(make_config().sensors) == []
    assert _monitor([]).reading is None


def test_configured_wind_sensors_replace_the_outside_weather_entities():
    config = make_config({CONF_WIND_SENSOR_ENTITY_ID: [ANEMOMETER], CONF_OUTSIDE_SENSOR_ENTITY_ID: [WEATHER]}).sensors

    assert WindMonitor.sources(config) == [ANEMOMETER]


async def test_sensor_speed_is_converted_to_kilometers_per_hour(hass):
    monitor = _monitor([ANEMOMETER])
    _set_anemometer(hass, "5")

    monitor.observe(hass, timestamp())

    assert monitor.reading == WindReading(speed=18.0, entity_id=ANEMOMETER)


async def test_weather_speed_uses_its_own_unit(hass):
    monitor = _monitor([], outside=[WEATHER])
    _set_weather(hass, 10.0, "m/s")

    monitor.observe(hass, timestamp())

    assert monitor.reading == WindReading(speed=36.0, entity_id=WEATHER)


async def test_falls_back_to_the_next_source_while_the_first_is_unavailable(hass):
    monitor = _monitor([ANEMOMETER, WEATHER])
    hass.states.async_set(ANEMOMETER, STATE_UNAVAILABLE)
    _set_weather(hass, 25.0)

    monitor.observe(hass, timestamp())

    assert monitor.reading == WindReading(speed=25.0, entity_id=WEATHER)


async def test_sensor_without_a_speed_unit_is_skipped(hass):
    monitor = _monitor([ANEMOMETER])
    _set_anemometer(hass, "5", unit="%")

    monitor.observe(hass, timestamp())

    assert monitor.reading is None


async def test_silent_sensor_is_stale(hass, freezer):
    monitor = _monitor([ANEMOMETER, WEATHER])
    _set_anemometer(hass, "0")
    freezer.tick(timedelta(hours=7))
    _set_weather(hass, 30.0)

    monitor.observe(hass, timestamp())

    assert monitor.reading.entity_id == WEATHER


async def test_calm_sensor_that_keeps_reporting_stays_fresh(hass, freezer):
    monitor = _monitor([ANEMOMETER, WEATHER])
    _set_weather(hass, 30.0)
    _set_anemometer(hass, "0")
    freezer.tick(timedelta(hours=4))
    _set_anemometer(hass, "0")
    freezer.tick(timedelta(hours=4))

    monitor.observe(hass, timestamp())

    assert monitor.reading == WindReading(speed=0.0, entity_id=ANEMOMETER)


async def test_reading_is_the_mean_over_the_smoothing_window(hass):
    monitor = _monitor([], outside=[WEATHER])
    now = timestamp()

    for offset, speed in ((0.0, 50.0), (1800.0, 10.0), (3600.0, 20.0)):
        _set_weather(hass, speed)
        monitor.observe(hass, now + offset)

    assert monitor.reading.speed == pytest.approx(80.0 / 3, abs=0.1)

    _set_weather(hass, 30.0)
    monitor.observe(hass, now + WIND_SMOOTHING_WINDOW_SECONDS + 1800.0)

    assert monitor.reading.speed == pytest.approx(20.0)


async def test_readings_of_another_source_are_not_averaged_together(hass):
    monitor = _monitor([ANEMOMETER, WEATHER])
    now = timestamp()
    _set_weather(hass, 40.0)
    _set_anemometer(hass, "2")
    monitor.observe(hass, now)

    hass.states.async_set(ANEMOMETER, STATE_UNAVAILABLE)
    monitor.observe(hass, now + 60.0)

    assert monitor.reading == WindReading(speed=40.0, entity_id=WEATHER)


def test_hourly_history_prefers_earlier_statistics_and_fills_their_gaps():
    rows = {
        ANEMOMETER: [{"start": 7200.0, "mean": 10.04}, {"start": 10800.0, "mean": None}],
        "sensor.sat_wind_speed": [{"start": 7200.0, "mean": 40.0}, {"start": 10800.0, "mean": 25.0}],
    }

    assert WindHistory.hourly(rows, [ANEMOMETER, "sensor.sat_wind_speed"]) == {
        7200.0: WindReading(speed=10.0, entity_id=ANEMOMETER),
        10800.0: WindReading(speed=25.0, entity_id="sensor.sat_wind_speed"),
    }


def test_hour_is_the_start_of_the_hour():
    assert WindHistory.hour(7200.0 + 3599.0) == 7200.0


async def test_hourly_history_is_empty_without_a_recorder(hass):
    history = WindHistory("entry", make_config({CONF_WIND_SENSOR_ENTITY_ID: [ANEMOMETER]}).sensors)

    assert await history.async_hourly(hass, timestamp() - 7200.0) == {}


def _import_wind(hass, statistic_id: str, unit: str, means: dict) -> None:
    async_import_statistics(
        hass,
        {
            "has_sum": False,
            "mean_type": StatisticMeanType.ARITHMETIC,
            "name": None,
            "source": "recorder",
            "statistic_id": statistic_id,
            "unit_class": SpeedConverter.UNIT_CLASS,
            "unit_of_measurement": unit,
        },
        [{"start": start, "mean": value, "min": value, "max": value} for start, value in means.items()],
    )


async def test_hourly_history_from_real_statistics(recorder_mock, hass):
    mirror = er.async_get(hass).async_get_or_create("sensor", DOMAIN, "entry-wind-speed", suggested_object_id="sat_wind_speed")
    hours = [dt_util.start_of_local_day() - timedelta(hours=offset) for offset in (3, 2)]
    _import_wind(hass, ANEMOMETER, "m/s", {hours[0]: 5.0})
    _import_wind(hass, mirror.entity_id, "km/h", {hours[0]: 99.0, hours[1]: 30.0})
    await async_wait_recording_done(hass)

    history = WindHistory("entry", make_config({CONF_WIND_SENSOR_ENTITY_ID: [WEATHER, ANEMOMETER]}).sensors)
    hourly = await history.async_hourly(hass, hours[0].timestamp() + 600.0)

    assert hourly == {
        hours[0].timestamp(): WindReading(speed=18.0, entity_id=ANEMOMETER),
        hours[1].timestamp(): WindReading(speed=30.0, entity_id=mirror.entity_id),
    }
