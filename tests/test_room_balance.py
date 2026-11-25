"""Tests for the per-room balance advisor."""

import json
import re
from collections import deque
from dataclasses import replace
from datetime import date, datetime, timedelta
from math import exp
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.sat
from custom_components.sat.const import CONF_HEATING_SYSTEM, CONF_ROOMS, DOMAIN
from custom_components.sat.diagnostics import async_get_config_entry_diagnostics
from custom_components.sat.heating_curve import RECOMMENDATION_STEADY_WINDOW_SECONDS
from custom_components.sat.room_balance import (
    BoilerObservation,
    CoolingPoint,
    MAIN_ROOM_ID,
    RoomBalanceDay,
    RoomBalanceEvaluator,
    RoomBalanceMonitor,
    RoomCoolingTracker,
    RoomHeatRiseTracker,
    RoomObservation,
    RoomSignalReading,
    RoomSignals,
    RoomWarmUpTracker,
    SAMPLE_INTERVAL_SECONDS,
    WarmUpPoint,
)
from custom_components.sat.types import HeatingSystem, RoomBalanceStatus

ROOM = "climate.bedroom"
TRANSLATIONS = Path(custom_components.sat.__file__).parent / "translations"
DAY_SECONDS = 24 * 60 * 60
START = dt_util.as_utc(datetime(2026, 11, 1, 12, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE)).timestamp()

WITH_ROOM = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [([], {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS, CONF_ROOMS: [ROOM]}, {}, {})],
)


def _boiler(eligible: bool = True, heating: bool = True, flame_active: bool = True, flow_temperature: float = 50.0, **kwargs) -> BoilerObservation:
    return BoilerObservation(eligible=eligible, heating=heating, flame_active=flame_active, flow_temperature=flow_temperature, **kwargs)


def _room(room_id: str = ROOM, **kwargs) -> RoomObservation:
    values = dict(steady=True, calling=True, shape=20.0, shortfall=1.5, temperature=19.0, implied_coefficient=2.5)
    values.update(kwargs)

    return RoomObservation(room_id=room_id, **values)


def _monitor() -> RoomBalanceMonitor:
    return RoomBalanceMonitor("entry", [ROOM])


def _complete_day(monitor: RoomBalanceMonitor, now: float = START) -> None:
    monitor.observe(_boiler(eligible=False), [], now + DAY_SECONDS)


def test_room_far_below_target_still_qualifies():
    monitor = _monitor()

    monitor.observe(_boiler(), [_room(shortfall=3.0)], START)
    _complete_day(monitor)

    assert monitor.days[0].shortfall == 3.0
    assert monitor.days[0].sample_count == 1
    assert monitor.days[0].implied_coefficient == 2.5


@pytest.mark.parametrize("boiler", [_boiler(eligible=False), _boiler(heating=False)])
def test_ineligible_system_records_nothing(boiler):
    monitor = _monitor()

    monitor.observe(boiler, [_room()], START)
    _complete_day(monitor)

    assert monitor.days == []


@pytest.mark.parametrize("room", [
    _room(steady=False),
    _room(implied_coefficient=None),
    _room(shortfall=None),
    _room(signals=RoomSignalReading(window_open=True)),
    _room(signals=RoomSignalReading(valve_alarm=True)),
])
def test_unusable_room_records_nothing(room):
    monitor = _monitor()

    monitor.observe(_boiler(), [room], START)
    _complete_day(monitor)

    assert monitor.days == []


def test_samples_are_limited_to_the_interval():
    monitor = _monitor()

    monitor.observe(_boiler(), [_room()], START)
    monitor.observe(_boiler(), [_room()], START + SAMPLE_INTERVAL_SECONDS - 1)
    monitor.observe(_boiler(), [_room()], START + SAMPLE_INTERVAL_SECONDS)
    _complete_day(monitor)

    assert monitor.days[0].sample_count == 2


def test_calling_fraction_and_valve_are_summarised():
    monitor = _monitor()

    monitor.observe(_boiler(), [_room(calling=True, valve_position=100.0)], START)
    monitor.observe(_boiler(), [_room(calling=False, valve_position=60.0)], START + SAMPLE_INTERVAL_SECONDS)
    _complete_day(monitor)

    assert monitor.days[0].calling_fraction == 0.5
    assert monitor.days[0].valve_position == 80.0


def test_valve_position_stays_empty_when_not_reported():
    monitor = _monitor()

    monitor.observe(_boiler(), [_room()], START)
    _complete_day(monitor)

    assert monitor.days[0].valve_position is None


async def test_history_survives_a_reload_and_drops_unknown_rooms(hass):
    monitor = RoomBalanceMonitor("entry", [ROOM, "climate.removed"])
    await monitor.async_added_to_hass(hass)

    monitor.observe(_boiler(), [_room(), _room("climate.removed")], START)
    _complete_day(monitor)
    monitor.observe(_boiler(), [_room(implied_coefficient=3.0)], START + DAY_SECONDS + 1)
    await monitor.async_save_data()

    restored = _monitor()
    await restored.async_added_to_hass(hass)

    assert [day.room_id for day in restored.days] == [ROOM]

    restored.observe(_boiler(eligible=False), [], START + 2 * DAY_SECONDS)
    assert restored.days[1].implied_coefficient == 3.0


def _point(timestamp: float, temperature: float, flow_temperature: float = 50.0) -> WarmUpPoint:
    return WarmUpPoint(timestamp=timestamp, temperature=temperature, flow_temperature=flow_temperature)


def test_warm_up_response_from_a_known_episode():
    tracker = RoomWarmUpTracker()

    assert tracker.update(True, _point(0.0, 19.0)) is None
    assert tracker.update(True, _point(900.0, 19.5)) is None

    response = tracker.update(True, _point(1800.0, 20.0))

    assert response == pytest.approx(2.0 / 30.5)


def test_warm_up_gap_restarts_the_episode():
    tracker = RoomWarmUpTracker()

    tracker.update(True, _point(0.0, 19.0))
    tracker.update(False, _point(900.0, 19.5))

    assert tracker.update(True, _point(1800.0, 20.0)) is None


def test_warm_up_ignores_a_cooling_room():
    tracker = RoomWarmUpTracker()

    tracker.update(True, _point(0.0, 19.0))

    assert tracker.update(True, _point(1800.0, 18.8)) is None


def test_warm_up_needs_a_room_below_target():
    monitor = _monitor()

    monitor.observe(_boiler(), [_room(shortfall=0.1, steady=False, temperature=19.0)], START)
    monitor.observe(_boiler(), [_room(shortfall=0.1, steady=False, temperature=20.0)], START + 1800)
    _complete_day(monitor)

    assert monitor.days == []


def test_monitor_records_warm_up_episodes():
    monitor = _monitor()

    monitor.observe(_boiler(), [_room(steady=False, temperature=19.0)], START)
    monitor.observe(_boiler(), [_room(steady=False, temperature=20.0)], START + 1800)
    _complete_day(monitor)

    assert monitor.days[0].warm_up_episodes == 1
    assert monitor.days[0].warm_up_response is not None
    assert monitor.days[0].sample_count == 0


def test_heat_rise_is_measured_over_a_flame_period():
    tracker = RoomHeatRiseTracker()

    assert tracker.update(True, 24.0, 0.0) is None
    assert tracker.update(True, 31.0, 900.0) is None
    assert tracker.update(False, 30.0, 1000.0) == 7.0


def test_short_flame_period_gives_no_heat_rise():
    tracker = RoomHeatRiseTracker()

    tracker.update(True, 24.0, 0.0)

    assert tracker.update(False, 25.0, 300.0) is None


def test_missing_device_temperature_gives_no_heat_rise():
    tracker = RoomHeatRiseTracker()

    tracker.update(True, None, 0.0)

    assert tracker.update(False, None, 900.0) is None


def _cooled(hours: float, time_constant: float = 40.0, start: float = 20.0, outside: float = 5.0) -> CoolingPoint:
    return CoolingPoint(timestamp=hours * 3600, temperature=outside + (start - outside) * exp(-hours / time_constant), outside_temperature=outside)


def test_known_cooling_curve_recovers_its_time_constant():
    tracker = RoomCoolingTracker()

    assert tracker.update(True, _cooled(0.0)) is None
    assert tracker.update(True, _cooled(1.0)) is None
    assert tracker.update(True, _cooled(2.0)) == pytest.approx(40.0)


def test_cooling_needs_enough_drop():
    tracker = RoomCoolingTracker()

    tracker.update(True, CoolingPoint(timestamp=0.0, temperature=20.0, outside_temperature=5.0))

    assert tracker.update(True, CoolingPoint(timestamp=7200.0, temperature=19.8, outside_temperature=5.0)) is None


def test_cooling_needs_a_difference_with_outside():
    tracker = RoomCoolingTracker()

    tracker.update(True, CoolingPoint(timestamp=0.0, temperature=20.0, outside_temperature=16.0))

    assert tracker.update(True, CoolingPoint(timestamp=7200.0, temperature=19.0, outside_temperature=16.0)) is None


def test_inactive_period_restarts_cooling():
    tracker = RoomCoolingTracker()

    tracker.update(True, _cooled(0.0))
    tracker.update(False, _cooled(1.0))

    assert tracker.update(True, _cooled(2.0)) is None


@pytest.mark.parametrize("boiler", [
    _boiler(flame_active=True, sun_below_horizon=True, outside_temperature=5.0),
    _boiler(flame_active=False, sun_below_horizon=False, outside_temperature=5.0),
    _boiler(flame_active=False, sun_below_horizon=True, outside_temperature=None),
])
def test_monitor_only_measures_cooling_with_the_flame_off_after_sunset(boiler):
    monitor = _monitor()

    monitor.observe(boiler, [_room(steady=False, temperature=20.0, shortfall=0.0)], START)
    monitor.observe(boiler, [_room(steady=False, temperature=19.0, shortfall=0.0)], START + 7200)
    _complete_day(monitor, START)

    assert monitor.days == [] or monitor.days[0].cooling_time_constant is None


def test_monitor_records_cooling_without_changing_the_verdict():
    monitor = _monitor()
    boiler = _boiler(flame_active=False, sun_below_horizon=True, outside_temperature=5.0)

    monitor.observe(boiler, [_room(steady=False, temperature=20.0, shortfall=0.0)], START)
    monitor.observe(boiler, [_room(steady=False, temperature=19.0, shortfall=0.0)], START + 7200)
    _complete_day(monitor)

    assert monitor.days[0].cooling_time_constant > 0

    plain = _evaluate(_days("cold", coefficient=3.0), _days("a"), _days("b"))
    cooling = _evaluate([replace(day, cooling_time_constant=5.0) for day in _days("cold", coefficient=3.0)], _days("a"), _days("b"))

    assert cooling["cold"].cooling_time_constant == 5.0
    assert cooling["cold"].status == plain["cold"].status


def _device_with_entities(hass, entities: list[dict]) -> None:
    source = MockConfigEntry(domain="mqtt")
    source.add_to_hass(hass)

    device = dr.async_get(hass).async_get_or_create(config_entry_id=source.entry_id, identifiers={("mqtt", "trv")})
    registry = er.async_get(hass)

    registry.async_get_or_create("climate", "mqtt", "trv_climate", suggested_object_id="bedroom", device_id=device.id)

    for entity in entities:
        registry.async_get_or_create(
            entity["domain"], "mqtt", entity["unique_id"], suggested_object_id=entity["unique_id"], device_id=device.id,
            original_device_class=entity.get("device_class"), disabled_by=entity.get("disabled_by"),
        )


async def test_signals_are_found_on_a_zigbee2mqtt_shaped_device(hass):
    _device_with_entities(hass, [
        {"domain": "binary_sensor", "unique_id": "0x54_window_open_zigbee2mqtt", "device_class": "window"},
        {"domain": "binary_sensor", "unique_id": "0x54_valve_alarm_zigbee2mqtt", "device_class": "problem"},
        {"domain": "sensor", "unique_id": "0x54_device_temperature_zigbee2mqtt", "device_class": "temperature"},
        {"domain": "sensor", "unique_id": "0x54_battery_zigbee2mqtt", "device_class": "battery"},
    ])

    hass.states.async_set("binary_sensor.0x54_window_open_zigbee2mqtt", "on")
    hass.states.async_set("binary_sensor.0x54_valve_alarm_zigbee2mqtt", "off")
    hass.states.async_set("sensor.0x54_device_temperature_zigbee2mqtt", "27.5")

    assert RoomSignals.read(hass, ROOM) == RoomSignalReading(window_open=True, valve_alarm=False, device_temperature=27.5)


async def test_device_without_signals_reads_empty(hass):
    _device_with_entities(hass, [])

    assert RoomSignals.read(hass, ROOM) == RoomSignalReading()


async def test_unregistered_climate_reads_empty(hass):
    hass.states.async_set("climate.yaml_only", HVACMode.HEAT)

    assert RoomSignals.read(hass, "climate.yaml_only") == RoomSignalReading()


async def test_ambiguous_temperature_sensors_are_not_used(hass):
    _device_with_entities(hass, [
        {"domain": "sensor", "unique_id": "first_temperature", "device_class": "temperature"},
        {"domain": "sensor", "unique_id": "second_temperature", "device_class": "temperature"},
    ])

    hass.states.async_set("sensor.first_temperature", "20.0")
    hass.states.async_set("sensor.second_temperature", "21.0")

    assert RoomSignals.read(hass, ROOM).device_temperature is None


async def test_single_generic_temperature_sensor_is_used(hass):
    _device_with_entities(hass, [{"domain": "sensor", "unique_id": "head_temperature", "device_class": "temperature"}])
    hass.states.async_set("sensor.head_temperature", "26.0")

    assert RoomSignals.read(hass, ROOM).device_temperature == 26.0


async def test_disabled_entities_are_ignored(hass):
    _device_with_entities(hass, [
        {"domain": "sensor", "unique_id": "0x54_device_temperature", "device_class": "temperature", "disabled_by": er.RegistryEntryDisabler.INTEGRATION},
    ])

    assert RoomSignals.read(hass, ROOM).device_temperature is None


@WITH_ROOM
async def test_calling_uses_hvac_action_when_present(hass, climate, demand_control, domains, data, options, config):
    area = demand_control.areas.get(ROOM)

    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 19.0, "hvac_action": HVACAction.IDLE})
    assert area.is_calling is False

    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 22.0, "hvac_action": HVACAction.HEATING})
    assert area.is_calling is True


@WITH_ROOM
async def test_calling_falls_back_to_the_reported_temperature(hass, climate, demand_control, domains, data, options, config):
    area = demand_control.areas.get(ROOM)

    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 19.0})
    assert area.is_calling is True

    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 22.0})
    assert area.is_calling is False


@WITH_ROOM
async def test_target_change_breaks_room_steadiness(hass, climate, demand_control, domains, data, options, config):
    area = demand_control.areas.get(ROOM)
    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 19.0})

    start = dt_util.utcnow()
    steps = 8

    for index in range(steps):
        area.control_pid(start + (index + 1) * (RECOMMENDATION_STEADY_WINDOW_SECONDS / steps) * dt_util.dt.timedelta(seconds=1))

    assert area.is_steady is True

    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 23.0, "current_temperature": 19.0})
    area.control_pid(start + (steps + 1) * (RECOMMENDATION_STEADY_WINDOW_SECONDS / steps) * dt_util.dt.timedelta(seconds=1))

    assert area.is_steady is False


@pytest.fixture
async def room_states(hass):
    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 19.0})
    hass.states.async_set("sensor.test_inside_sensor", "20.0")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")


@WITH_ROOM
async def test_climate_feeds_the_monitor_without_valve_data(room_states, hass, climate, demand_control, monkeypatch, domains, data, options, config):
    demand_control.areas.get(ROOM).control_pid()

    seen = []
    monkeypatch.setattr(demand_control.room_balance, "observe", lambda boiler, rooms, now: seen.append((boiler, rooms)))

    demand_control._record_room_observations(START, SimpleNamespace(error=0.5, current=20.5))

    boiler, rooms = seen[0]
    assert [room.room_id for room in rooms] == [MAIN_ROOM_ID, ROOM]
    assert rooms[1].shortfall == 2.0
    assert rooms[1].valve_position is None
    assert rooms[1].signals == RoomSignalReading()
    assert rooms[1].implied_coefficient is not None


TODAY = date(2026, 11, 15)


def _days(room_id: str, coefficient: float = 2.0, shortfall: float = 0.0, calling: float = 0.8, response: Optional[float] = None, valve: Optional[float] = None, count: int = 7, samples: int = 24) -> list[RoomBalanceDay]:
    return [
        RoomBalanceDay(
            date=(TODAY - timedelta(days=age)).isoformat(),
            room_id=room_id,
            sample_count=samples,
            calling_fraction=calling,
            shape=20.0,
            shortfall=shortfall,
            valve_position=valve,
            warm_up_response=response,
            warm_up_episodes=1 if response is not None else 0,
            implied_coefficient=coefficient,
        )
        for age in range(1, count + 1)
    ]


def _evaluate(*days: list[RoomBalanceDay]) -> dict:
    return RoomBalanceEvaluator.evaluate([day for room_days in days for day in room_days], TODAY.toordinal())


def test_two_signals_without_a_valve_are_undersupplied():
    result = _evaluate(_days("cold", coefficient=3.0, shortfall=1.0), _days("a"), _days("b"))

    assert result["cold"].status == RoomBalanceStatus.UNDERSUPPLIED
    assert result["cold"].valve_position is None
    assert result["a"].status == RoomBalanceStatus.BALANCED


def test_one_signal_without_a_valve_is_not_undersupplied():
    high_demand = _evaluate(_days("cold", coefficient=3.0), _days("a"), _days("b"))
    falling_short = _evaluate(_days("cold", shortfall=1.0), _days("a"), _days("b"))

    assert high_demand["cold"].status == RoomBalanceStatus.BALANCED
    assert falling_short["cold"].status == RoomBalanceStatus.BALANCED


def test_slow_response_counts_as_a_signal():
    result = _evaluate(_days("cold", shortfall=1.0, response=0.02), _days("a", response=0.05), _days("b", response=0.05))

    assert result["cold"].status == RoomBalanceStatus.UNDERSUPPLIED


def test_response_needs_enough_episodes():
    result = _evaluate(_days("cold", shortfall=1.0, response=0.02, count=2) + _days("cold", shortfall=1.0)[2:], _days("a", response=0.05), _days("b", response=0.05))

    assert result["cold"].warm_up_episodes == 2
    assert result["cold"].status == RoomBalanceStatus.BALANCED


def test_short_room_with_an_idle_thermostat_is_a_sensor_mismatch():
    result = _evaluate(_days("cold", coefficient=3.0, shortfall=1.0, calling=0.1), _days("a"), _days("b"))

    assert result["cold"].status == RoomBalanceStatus.SENSOR_MISMATCH


def test_fast_room_is_oversupplied_only_next_to_an_undersupplied_room():
    with_short = _evaluate(_days("cold", coefficient=3.0, shortfall=1.0), _days("fast", response=0.09), _days("a", response=0.05), _days("b", response=0.05))
    without_short = _evaluate(_days("fast", response=0.09), _days("a", response=0.05), _days("b", response=0.05))

    assert with_short["fast"].status == RoomBalanceStatus.OVERSUPPLIED
    assert without_short["fast"].status == RoomBalanceStatus.BALANCED


def test_single_room_has_insufficient_data():
    assert _evaluate(_days("only", coefficient=3.0, shortfall=1.0))["only"].status == RoomBalanceStatus.INSUFFICIENT_DATA


def test_room_needs_enough_qualifying_days():
    result = _evaluate(_days("few", coefficient=3.0, shortfall=1.0, count=4), _days("a"), _days("b"))

    assert result["few"].status == RoomBalanceStatus.INSUFFICIENT_DATA
    assert result["few"].days == 4


def test_days_with_too_few_samples_do_not_qualify():
    assert _evaluate(_days("few", samples=11), _days("a"), _days("b"))["few"].days == 0


def test_old_days_are_outside_the_window():
    old = [replace(day, date=(TODAY - timedelta(days=30)).isoformat()) for day in _days("old", coefficient=5.0, shortfall=2.0)]

    assert "old" not in _evaluate(old, _days("a"), _days("b"))


def test_flow_premium_is_calculated_from_the_reference():
    result = _evaluate(_days("cold", coefficient=3.0, shortfall=1.0), _days("a"), _days("b"))

    assert result["cold"].reference_coefficient == 2.0
    assert result["cold"].flow_premium == 5.0
    assert result["a"].flow_premium == 0.0


def test_one_signal_with_a_saturated_valve_is_undersupplied():
    result = _evaluate(_days("cold", coefficient=3.0, valve=95.0), _days("a"), _days("b"))

    assert result["cold"].status == RoomBalanceStatus.UNDERSUPPLIED


def test_half_open_valve_does_not_confirm_a_single_signal():
    result = _evaluate(_days("cold", coefficient=3.0, valve=50.0), _days("a"), _days("b"))

    assert result["cold"].status == RoomBalanceStatus.BALANCED


def test_low_valve_median_is_oversupplied_next_to_an_undersupplied_room():
    result = _evaluate(_days("cold", coefficient=3.0, shortfall=1.0), _days("open", valve=20.0), _days("a"))

    assert result["open"].status == RoomBalanceStatus.OVERSUPPLIED


def test_heat_rise_changes_no_verdict():
    plain = _evaluate(_days("cold", coefficient=3.0), _days("a"), _days("b"))
    rising = [replace(day, heat_rise=10.0) for day in _days("cold", coefficient=3.0)]

    result = _evaluate(rising, _days("a"), _days("b"))

    assert result["cold"].heat_rise == 10.0
    assert result["cold"].status == plain["cold"].status


def test_reported_fault_takes_precedence_and_clears():
    monitor = _monitor()

    monitor.observe(_boiler(), [_room(signals=RoomSignalReading(valve_alarm=True))], START)
    assert monitor.balance(ROOM).status == RoomBalanceStatus.ROOM_FAULT

    monitor.observe(_boiler(), [_room()], START + 1)
    assert monitor.balance(ROOM).status == RoomBalanceStatus.INSUFFICIENT_DATA


def _inject_days(monitor: RoomBalanceMonitor, days: list[RoomBalanceDay]) -> None:
    today = dt_util.now().date()
    shift = today.toordinal() - TODAY.toordinal()

    monitor._days = deque(replace(day, date=date.fromordinal(day.ordinal + shift).isoformat()) for day in days)
    monitor._current_date = today.isoformat()
    monitor._balances = None


@WITH_ROOM
async def test_room_sensor_exposes_the_balance(room_states, hass, climate, demand_control, coordinator, entry, domains, data, options, config):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", "sat", f"{entry.entry_id}-{ROOM}-room-balance")

    assert hass.states.get(entity_id).state == "insufficient_data"

    _inject_days(demand_control.room_balance, _days(ROOM, coefficient=3.0, shortfall=1.0) + _days(MAIN_ROOM_ID) + _days("climate.other"))
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "undersupplied"
    assert state.attributes["flow_premium"] == 5.0
    assert state.attributes["valve_position"] is None
    assert state.attributes["radiator_heat_rise"] is None


@WITH_ROOM
@pytest.mark.parametrize(("key", "room_days"), [
    ("room_undersupplied", {"coefficient": 3.0, "shortfall": 1.0}),
    ("room_sensor_mismatch", {"coefficient": 3.0, "shortfall": 1.0, "calling": 0.1}),
])
async def test_repair_issue_is_raised_and_cleared(room_states, hass, climate, demand_control, coordinator, entry, domains, data, options, config, key, room_days):
    issue_id = f"{key}_{entry.entry_id}_{ROOM}"
    issues = ir.async_get(hass)

    _inject_days(demand_control.room_balance, _days(ROOM, **room_days) + _days(MAIN_ROOM_ID) + _days("climate.other"))
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == key
    assert issue.translation_placeholders["room"] == hass.states.get(ROOM).name

    _inject_days(demand_control.room_balance, [])
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert issues.async_get_issue(DOMAIN, issue_id) is None


@WITH_ROOM
async def test_main_climate_has_its_own_balance_sensor_and_issue(room_states, hass, climate, demand_control, coordinator, entry, domains, data, options, config):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", "sat", f"{entry.entry_id}-{MAIN_ROOM_ID}-room-balance")
    issue_id = f"room_undersupplied_{entry.entry_id}_{MAIN_ROOM_ID}"

    assert hass.states.get(entity_id).state == "insufficient_data"

    _inject_days(demand_control.room_balance, _days(MAIN_ROOM_ID, coefficient=3.0, shortfall=1.0) + _days(ROOM) + _days("climate.other"))
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "undersupplied"

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue.translation_placeholders["room"] == hass.states.get(climate.entity_id).name


@pytest.mark.parametrize(("domains", "data", "options", "config"), [([], {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, {}, {})])
async def test_main_climate_has_a_balance_sensor_without_rooms(hass, climate, entry, domains, data, options, config):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", "sat", f"{entry.entry_id}-{MAIN_ROOM_ID}-room-balance")

    assert hass.states.get(entity_id).state == "insufficient_data"


@WITH_ROOM
async def test_fault_issue_follows_the_alarm(room_states, hass, climate, demand_control, coordinator, entry, domains, data, options, config):
    issue_id = f"room_fault_{entry.entry_id}_{ROOM}"
    issues = ir.async_get(hass)

    demand_control.room_balance._faults = {ROOM}
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert issues.async_get_issue(DOMAIN, issue_id) is not None

    demand_control.room_balance._faults = set()
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert issues.async_get_issue(DOMAIN, issue_id) is None


@WITH_ROOM
async def test_balanced_room_raises_no_issue(room_states, hass, climate, demand_control, coordinator, entry, domains, data, options, config):
    _inject_days(demand_control.room_balance, _days(ROOM) + _days(MAIN_ROOM_ID) + _days("climate.other"))
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert [issue for issue in ir.async_get(hass).issues if issue[1].startswith("room_")] == []


@pytest.mark.parametrize("key", ["room_undersupplied", "room_sensor_mismatch", "room_fault"])
def test_issues_are_translated_in_every_language(key):
    placeholders = None

    for path in sorted(TRANSLATIONS.glob("*.json")):
        issue = json.loads(path.read_text(encoding="utf-8"))["issues"][key]
        found = set(re.findall(r"{(\w+)}", issue["description"]))

        assert issue["title"]
        assert placeholders is None or found == placeholders
        placeholders = found


def test_states_are_translated_in_every_language():
    expected = {status.value for status in RoomBalanceStatus}

    for path in sorted(TRANSLATIONS.glob("*.json")):
        assert set(json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"]["room_balance"]["state"]) == expected


@WITH_ROOM
async def test_diagnostics_include_the_room_balance(room_states, hass, climate, entry, domains, data, options, config):
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["climate"]["room_balance"][ROOM]["status"] == RoomBalanceStatus.INSUFFICIENT_DATA


@pytest.mark.parametrize(("domains", "data", "options", "config"), [([], {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, {}, {})])
async def test_climate_feeds_both_monitors_without_rooms(hass, climate, demand_control, monkeypatch, domains, data, options, config):
    balance, preheat = [], []
    monkeypatch.setattr(demand_control.room_balance, "observe", lambda boiler, rooms, now: balance.append(rooms))
    monkeypatch.setattr(demand_control.preheat, "observe", lambda boiler, rooms, now: preheat.append(rooms))

    demand_control._record_room_observations(START, SimpleNamespace(error=0.5, current=20.5))

    assert [room.room_id for room in balance[0]] == [MAIN_ROOM_ID]
    assert [room.room_id for room in preheat[0]] == [MAIN_ROOM_ID]


@WITH_ROOM
async def test_blocked_room_is_not_calling(hass, climate, demand_control, domains, data, options, config):
    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 19.0, "heating_blocked_by": "devices_released"})

    assert demand_control.areas.get(ROOM).is_calling is False
