"""Tests for learning how long rooms take to recover."""

from collections import deque
from datetime import datetime, timedelta
from math import ceil
from pathlib import Path

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import load_yaml_dict
from pytest_homeassistant_custom_component.common import async_mock_service

import custom_components.sat
from custom_components.sat.const import CONF_HEATING_SYSTEM, CONF_OUTSIDE_SENSOR_ENTITY_ID, CONF_ROOMS, DOMAIN, SERVICE_ESTIMATE_PREHEAT
from custom_components.sat.diagnostics import async_get_config_entry_diagnostics
from custom_components.sat.preheat import PreheatEvaluator, PreheatMonitor, PreheatRate, RecoveryEpisode, RoomRecoveryTracker
from custom_components.sat.room_balance import BoilerObservation, MAIN_ROOM_ID, RoomObservation, RoomSignalReading
from custom_components.sat.types import HeatingSystem

ROOM = "climate.bedroom"
WEATHER = "weather.home"
HOUR = 3600.0


def _boiler(eligible: bool = True, flame_active: bool = True, outside_temperature: float = 5.0) -> BoilerObservation:
    return BoilerObservation(eligible=eligible, heating=True, flame_active=flame_active, flow_temperature=50.0, outside_temperature=outside_temperature)


def _room(shortfall, room_id: str = ROOM, **kwargs) -> RoomObservation:
    return RoomObservation(room_id=room_id, shortfall=shortfall, **kwargs)


def _episode(rate: float, outside: float = 5.0, room_id: str = ROOM) -> RecoveryEpisode:
    return RecoveryEpisode(date="2026-11-01", room_id=room_id, degrees=2.0, rate_per_hour=rate, outside_temperature=outside)


def test_known_recovery_gives_the_right_rate():
    tracker = RoomRecoveryTracker()

    assert tracker.update(_boiler(), _room(2.0), 0.0) is None
    assert tracker.update(_boiler(), _room(1.0), HOUR) is None

    episode = tracker.update(_boiler(outside_temperature=3.0), _room(0.0), 2 * HOUR)

    assert episode.rate_per_hour == 1.0
    assert episode.degrees == 2.0
    assert episode.outside_temperature == 4.0


def test_flame_gaps_keep_the_recovery():
    tracker = RoomRecoveryTracker()

    tracker.update(_boiler(), _room(2.0), 0.0)
    tracker.update(_boiler(flame_active=False), _room(1.0), HOUR)

    assert tracker.update(_boiler(), _room(0.0), 2 * HOUR) is not None


@pytest.mark.parametrize(("boiler", "room"), [
    (_boiler(eligible=False), _room(1.0)),
    (_boiler(), _room(1.0, signals=RoomSignalReading(window_open=True))),
    (_boiler(), _room(1.0, signals=RoomSignalReading(valve_alarm=True))),
    (_boiler(), _room(None)),
])
def test_interruption_discards_the_recovery(boiler, room):
    tracker = RoomRecoveryTracker()

    tracker.update(_boiler(), _room(2.0), 0.0)
    tracker.update(boiler, room, HOUR)

    assert tracker.update(_boiler(), _room(0.0), 2 * HOUR) is None


def test_raised_target_discards_the_recovery():
    tracker = RoomRecoveryTracker()

    tracker.update(_boiler(), _room(2.0), 0.0)
    tracker.update(_boiler(), _room(2.6), HOUR)

    assert tracker.update(_boiler(), _room(0.0), 2 * HOUR) is None


def test_recovery_past_the_limit_is_discarded():
    tracker = RoomRecoveryTracker()

    tracker.update(_boiler(), _room(2.0), 0.0)

    assert tracker.update(_boiler(), _room(0.0), 13 * HOUR) is None


def test_small_shortfall_does_not_start_a_recovery():
    tracker = RoomRecoveryTracker()

    tracker.update(_boiler(), _room(0.9), 0.0)

    assert tracker.update(_boiler(), _room(0.0), HOUR) is None


def test_same_band_is_preferred():
    episodes = [_episode(1.0, outside=6.0)] * 3 + [_episode(3.0, outside=-4.0)] * 3

    assert PreheatEvaluator.rate(episodes, 7.0) == PreheatRate(episodes=3, rate_per_hour=1.0)
    assert PreheatEvaluator.rate(episodes, -2.0) == PreheatRate(episodes=3, rate_per_hour=3.0)


def test_nearest_usable_band_is_used():
    episodes = [_episode(1.0, outside=6.0)] * 3 + [_episode(9.0, outside=-4.0)] * 2

    assert PreheatEvaluator.rate(episodes, -3.0).rate_per_hour == 1.0


def test_overall_median_is_used_when_no_band_is_usable():
    episodes = [_episode(1.0, outside=6.0), _episode(2.0, outside=-4.0), _episode(3.0, outside=14.0)]

    assert PreheatEvaluator.rate(episodes, 5.0) == PreheatRate(episodes=3, rate_per_hour=2.0)


def test_unknown_outside_temperature_uses_the_overall_median():
    assert PreheatEvaluator.rate([_episode(1.0)] * 3, None).rate_per_hour == 1.0


def test_too_few_episodes_give_no_rate():
    assert PreheatEvaluator.rate([_episode(1.0)] * 2, 5.0) is None


def test_lead_time_includes_the_margin_and_rounds_up():
    rate = PreheatRate(episodes=3, rate_per_hour=1.0)

    assert rate.lead_minutes(2.0) == 132
    assert rate.lead_minutes(0.0) == 0
    assert rate.lead_minutes(-1.0) == 0


def test_monitor_keeps_rooms_apart():
    monitor = PreheatMonitor("entry")

    monitor.observe(_boiler(), [_room(2.0), _room(1.0, room_id=MAIN_ROOM_ID)], 0.0)
    monitor.observe(_boiler(), [_room(0.0), _room(0.0, room_id=MAIN_ROOM_ID)], HOUR)

    assert monitor.episodes(ROOM)[0].rate_per_hour == 2.0
    assert monitor.episodes(MAIN_ROOM_ID)[0].rate_per_hour == 1.0


SETUP = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [([], {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS, CONF_ROOMS: [ROOM], CONF_OUTSIDE_SENSOR_ENTITY_ID: ["sensor.test_outside_sensor", WEATHER]}, {}, {})],
)


@pytest.fixture
async def room_states(hass):
    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 18.0})
    hass.states.async_set("sensor.test_inside_sensor", "19.0")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")
    hass.states.async_set(WEATHER, "cloudy", {"temperature": 5.0})


async def _estimate(hass, climate, **data):
    response = await hass.services.async_call(
        DOMAIN, SERVICE_ESTIMATE_PREHEAT, {"entity_id": climate.entity_id, **data}, blocking=True, return_response=True
    )

    return response[climate.entity_id]


def _forecast_at(moment: datetime, temperature: float) -> dict:
    return {"datetime": moment.isoformat(), "temperature": temperature}


@SETUP
async def test_service_estimates_the_main_thermostat(room_states, hass, climate, demand_control, domains, data, options, config):
    demand_control.preheat._episodes = deque([_episode(1.0, room_id=MAIN_ROOM_ID)] * 3)

    result = await _estimate(hass, climate, target_temperature=21.0)

    assert result["rate_per_hour"] == 1.0
    assert result["episodes"] == 3
    assert result["lead_minutes"] == ceil(2 * 60 * 1.1)
    assert result["start_time"] is None
    assert result["forecast_used"] is False


@SETUP
async def test_service_estimates_a_room_with_a_start_time(room_states, hass, climate, demand_control, domains, data, options, config):
    demand_control.preheat._episodes = deque([_episode(2.0)] * 3)
    ready = dt_util.now().replace(microsecond=0) + timedelta(hours=8)

    result = await _estimate(hass, climate, target_temperature=21.0, room=ROOM, ready_time=ready.isoformat())

    assert result["lead_minutes"] == ceil(1.5 * 60 * 1.1)
    assert dt_util.parse_datetime(result["start_time"]) == ready - timedelta(minutes=result["lead_minutes"])


@SETUP
async def test_service_reads_a_ready_time_without_timezone_as_local(room_states, hass, climate, demand_control, domains, data, options, config):
    demand_control.preheat._episodes = deque([_episode(2.0)] * 3)
    ready = dt_util.now().replace(microsecond=0, tzinfo=None) + timedelta(hours=8)

    result = await _estimate(hass, climate, target_temperature=21.0, room=ROOM, ready_time=ready.isoformat())

    expected = ready.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE) - timedelta(minutes=result["lead_minutes"])
    assert dt_util.parse_datetime(result["start_time"]) == expected


@SETUP
async def test_service_treats_an_empty_room_as_the_main_thermostat(room_states, hass, climate, demand_control, domains, data, options, config):
    demand_control.preheat._episodes = deque([_episode(1.0, room_id=MAIN_ROOM_ID)] * 3)

    assert (await _estimate(hass, climate, target_temperature=21.0, room=""))["rate_per_hour"] == 1.0


def _blueprint() -> dict:
    return load_yaml_dict(str(Path(custom_components.sat.__file__).parents[2] / "blueprints/automation/sat/adaptive_preheat.yaml"))


def _render(hass, template: str, **variables):
    return Template(template, hass).async_render(variables)


def test_blueprint_keeps_existing_inputs_and_adds_the_new_ones():
    inputs = _blueprint()["blueprint"]["input"]

    assert {"climate_entity", "ready_time", "target_temperature", "fallback_rate", "calendar_entity", "room_entity"} == set(inputs)
    assert inputs["calendar_entity"]["default"] == ""
    assert inputs["room_entity"]["default"] == ""


async def test_blueprint_ready_time_comes_from_the_calendar_or_the_clock(hass):
    ready = _blueprint()["variables"]["ready"]

    assert dt_util.parse_datetime(str(_render(hass, ready, calendar_entity="", ready_time="07:00:00"))).hour == 7

    hass.states.async_set("calendar.home", "off", {"start_time": "2030-01-02 16:30:00"})
    result = dt_util.parse_datetime(str(_render(hass, ready, calendar_entity="calendar.home", ready_time="07:00:00")))

    assert (result.year, result.hour, result.minute) == (2030, 16, 30)


async def test_blueprint_falls_back_without_an_event(hass):
    ready = _blueprint()["variables"]["ready"]
    hass.states.async_set("calendar.home", "off", {})

    assert dt_util.parse_datetime(str(_render(hass, ready, calendar_entity="calendar.home", ready_time="07:00:00"))).hour == 7


async def test_blueprint_lead_uses_the_learned_estimate_or_the_fallback(hass):
    lead = _blueprint()["actions"][1]["variables"]["lead_minutes"]
    climate_entity = "climate.sat"

    learned = _render(hass, lead, climate_entity=climate_entity, response={climate_entity: {"lead_minutes": 45}}, deficit=2.0, fallback_rate=1.0)
    fallback = _render(hass, lead, climate_entity=climate_entity, response={climate_entity: {"lead_minutes": None}}, deficit=2.0, fallback_rate=1.0)

    assert learned == 45
    assert fallback == 120


async def test_blueprint_deficit_is_never_negative_and_handles_a_missing_reading(hass):
    deficit = _blueprint()["actions"][1]["variables"]["deficit"]

    hass.states.async_set("climate.room", "heat", {"current_temperature": 18.0})
    assert _render(hass, deficit, target_entity="climate.room", target_temperature=20.0) == 2.0
    assert _render(hass, deficit, target_entity="climate.room", target_temperature=15.0) == 0

    hass.states.async_set("climate.room", "heat", {})
    assert _render(hass, deficit, target_entity="climate.room", target_temperature=20.0) == 0


@SETUP
async def test_service_rejects_an_unknown_room(room_states, hass, climate, domains, data, options, config):
    with pytest.raises(ServiceValidationError):
        await _estimate(hass, climate, target_temperature=21.0, room="climate.unknown")


@SETUP
async def test_service_without_an_estimate_returns_nulls(room_states, hass, climate, domains, data, options, config):
    result = await _estimate(hass, climate, target_temperature=21.0, ready_time=dt_util.now().isoformat())

    assert result["episodes"] == 0
    assert result["rate_per_hour"] is None
    assert result["lead_minutes"] is None
    assert result["start_time"] is None


@SETUP
async def test_service_with_no_deficit_gives_zero_lead(room_states, hass, climate, demand_control, domains, data, options, config):
    demand_control.preheat._episodes = deque([_episode(1.0, room_id=MAIN_ROOM_ID)] * 3)

    assert (await _estimate(hass, climate, target_temperature=15.0))["lead_minutes"] == 0


@SETUP
async def test_forecast_selects_the_band_for_the_ready_time(room_states, hass, climate, demand_control, domains, data, options, config):
    ready = dt_util.now().replace(microsecond=0) + timedelta(hours=8)
    forecast = [_forecast_at(ready - timedelta(hours=6), 20.0), _forecast_at(ready, -6.0), _forecast_at(ready + timedelta(hours=6), 20.0)]

    async_mock_service(hass, "weather", "get_forecasts", response={WEATHER: {"forecast": forecast}}, supports_response=SupportsResponse.ONLY)
    demand_control.preheat._episodes = deque([_episode(1.0, outside=5.0, room_id=MAIN_ROOM_ID)] * 3 + [_episode(0.5, outside=-7.0, room_id=MAIN_ROOM_ID)] * 3)

    result = await _estimate(hass, climate, target_temperature=21.0, ready_time=ready.isoformat())

    assert result["forecast_used"] is True
    assert result["outside_temperature"] == -6.0
    assert result["rate_per_hour"] == 0.5


@SETUP
async def test_forecast_too_far_from_the_ready_time_is_not_used(room_states, hass, climate, domains, data, options, config):
    ready = dt_util.now().replace(microsecond=0) + timedelta(hours=8)

    async_mock_service(hass, "weather", "get_forecasts", response={WEATHER: {"forecast": [_forecast_at(ready - timedelta(hours=6), -6.0)]}}, supports_response=SupportsResponse.ONLY)

    result = await _estimate(hass, climate, target_temperature=21.0, ready_time=ready.isoformat())

    assert result["forecast_used"] is False
    assert result["outside_temperature"] == 5.0


@SETUP
async def test_forecast_failure_falls_back_to_the_current_temperature(room_states, hass, climate, domains, data, options, config):
    result = await _estimate(hass, climate, target_temperature=21.0, ready_time=dt_util.now().isoformat())

    assert result["forecast_used"] is False
    assert result["outside_temperature"] == 5.0


@SETUP
async def test_diagnostics_include_the_learned_bands(room_states, hass, climate, demand_control, entry, domains, data, options, config):
    demand_control.preheat._episodes = deque([_episode(1.0, outside=6.0)] * 3)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["climate"]["preheat"][ROOM]["episodes"] == 3
    assert diagnostics["climate"]["preheat"][ROOM]["bands"][5] == {"episodes": 3, "rate_per_hour": 1.0}
    assert diagnostics["climate"]["preheat"][MAIN_ROOM_ID]["episodes"] == 0


async def test_episodes_survive_a_reload(hass):
    monitor = PreheatMonitor("entry")
    await monitor.async_added_to_hass(hass)

    monitor.observe(_boiler(), [_room(2.0)], 0.0)
    monitor.observe(_boiler(), [_room(0.0)], HOUR)
    await monitor.async_save_data()

    restored = PreheatMonitor("entry")
    await restored.async_added_to_hass(hass)

    assert restored.episodes(ROOM)[0].rate_per_hour == 2.0
