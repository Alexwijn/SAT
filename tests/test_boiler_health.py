"""Tests for the long-term boiler health monitor."""

import json
from collections import deque
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util

import custom_components.sat
from custom_components.sat.boiler_health import BoilerHealthDay, BoilerHealthMonitor, BoilerTrendEvaluator, HISTORY_RETENTION_DAYS, PRESSURE_SAMPLE_INTERVAL_SECONDS
from custom_components.sat.const import CONF_HEATING_SYSTEM, DOMAIN
from custom_components.sat.device import DeviceState
from custom_components.sat.diagnostics import async_get_config_entry_diagnostics
from custom_components.sat.types import CycleKind, HeatingSystem, Percentiles

START = dt_util.as_utc(datetime(2026, 11, 1, 12, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE)).timestamp()
DAY_SECONDS = 24 * 60 * 60


def _config(coefficient: float = 1.5):
    return SimpleNamespace(pid=f"pid-{coefficient}", pwm="pwm")


def _state(flow_temperature: float = 25.0, flame_active: bool = False) -> DeviceState:
    return DeviceState(
        flame_active=flame_active,
        central_heating=flame_active,
        hot_water_active=False,
        setpoint=None,
        flow_temperature=flow_temperature,
        return_temperature=None,
        max_modulation_level=None,
        relative_modulation_level=None,
    )


def _cycle(end: float, kind: CycleKind = CycleKind.CENTRAL_HEATING, duration: float = 600.0, delta: float = 8.0):
    metrics = SimpleNamespace(flow_return_delta=Percentiles(p50=delta, p90=delta))
    return SimpleNamespace(kind=kind, end=end, duration=duration, metrics=metrics)


def test_day_rollover_summarises_the_previous_day():
    monitor = BoilerHealthMonitor(_config())

    monitor.update(_state(), 1.5, START)
    monitor.update(_state(), 1.7, START + PRESSURE_SAMPLE_INTERVAL_SECONDS)
    monitor.update(_state(), 1.6, START + 2 * PRESSURE_SAMPLE_INTERVAL_SECONDS)
    monitor.record_cycle(_cycle(START + 60, duration=300.0, delta=6.0), 4.0)
    monitor.record_cycle(_cycle(START + 120, duration=900.0, delta=10.0), 6.0)

    assert monitor.days == []

    monitor.update(_state(), None, START + DAY_SECONDS)

    day = monitor.days[0]
    assert day.cycle_count == 2
    assert day.cold_pressure == 1.6
    assert day.median_on_seconds == 600.0
    assert day.flow_return_delta == 8.0
    assert day.outside_temperature == 5.0


def test_hot_water_cycles_are_ignored():
    monitor = BoilerHealthMonitor(_config())

    monitor.update(_state(), None, START)
    monitor.record_cycle(_cycle(START, kind=CycleKind.DOMESTIC_HOT_WATER), 5.0)
    monitor.update(_state(), None, START + DAY_SECONDS)

    assert monitor.days[0].cycle_count == 0


def test_pressure_is_only_sampled_while_cold():
    monitor = BoilerHealthMonitor(_config())
    step = PRESSURE_SAMPLE_INTERVAL_SECONDS

    monitor.update(_state(flow_temperature=60.0), 2.2, START)
    monitor.update(_state(flow_temperature=30.0, flame_active=True), 2.2, START + step)
    monitor.update(_state(flow_temperature=30.0), 1.5, START + 2 * step)
    monitor.update(_state(flow_temperature=30.0), 9.9, START + 2 * step + 1)
    monitor.update(_state(), None, START + DAY_SECONDS)

    assert monitor.days[0].cold_pressure == 1.5


def test_old_days_are_pruned():
    monitor = BoilerHealthMonitor(_config())

    for offset in range(HISTORY_RETENTION_DAYS + 10):
        monitor.update(_state(), None, START + offset * DAY_SECONDS)

    assert len(monitor.days) <= HISTORY_RETENTION_DAYS + 1


async def test_history_survives_a_reload(hass):
    monitor = BoilerHealthMonitor(_config())
    await monitor.async_added_to_hass(hass, "entry")

    monitor.update(_state(), 1.5, START)
    monitor.update(_state(), None, START + DAY_SECONDS)
    monitor.update(_state(), 1.4, START + DAY_SECONDS + PRESSURE_SAMPLE_INTERVAL_SECONDS)
    await monitor.async_save_data()

    restored = BoilerHealthMonitor(_config())
    await restored.async_added_to_hass(hass, "entry")

    assert restored.days[0].cold_pressure == 1.5

    restored.update(_state(), None, START + 2 * DAY_SECONDS)
    assert restored.days[1].cold_pressure == 1.4


async def test_config_change_clears_cycles_but_keeps_pressure(hass):
    monitor = BoilerHealthMonitor(_config(1.5))
    await monitor.async_added_to_hass(hass, "entry")

    monitor.update(_state(), 1.5, START)
    monitor.record_cycle(_cycle(START + 60), 5.0)
    monitor.update(_state(), None, START + DAY_SECONDS)
    monitor.record_cycle(_cycle(START + DAY_SECONDS + 60), 5.0)
    await monitor.async_save_data()

    restored = BoilerHealthMonitor(_config(2.0))
    await restored.async_added_to_hass(hass, "entry")

    assert restored.days[0].cold_pressure == 1.5
    assert restored.days[0].cycle_count == 0
    assert restored.days[0].median_on_seconds is None

    restored.update(_state(), None, START + 2 * DAY_SECONDS)
    assert restored.days[1].cycle_count == 0


TODAY = date(2026, 11, 1)


def _pressure_days(pressures: list[float]) -> list[BoilerHealthDay]:
    return [
        BoilerHealthDay(date=(TODAY - timedelta(days=len(pressures) - index)).isoformat(), cold_pressure=pressure)
        for index, pressure in enumerate(pressures)
    ]


def _pressure_trend(pressures: list[float]):
    return BoilerTrendEvaluator.pressure(_pressure_days(pressures), TODAY.toordinal())


def test_steady_pressure_loss_is_a_problem():
    trend = _pressure_trend([round(1.8 - index * 0.02, 2) for index in range(20)])

    assert trend.is_slow_loss
    assert trend.is_problem
    assert trend.top_ups == 0
    assert trend.loss_bar == pytest.approx(0.38, abs=0.01)


def test_quantised_flat_pressure_is_not_a_problem():
    trend = _pressure_trend([1.5, 1.6, 1.5, 1.5, 1.6, 1.5, 1.6, 1.6, 1.5, 1.5, 1.6, 1.5, 1.5, 1.6, 1.5, 1.6, 1.5, 1.5, 1.6, 1.5])

    assert trend.is_problem is False


def test_too_little_history_is_not_a_problem():
    trend = _pressure_trend([round(1.8 - index * 0.05, 2) for index in range(13)])

    assert trend.loss_bar is None
    assert trend.is_problem is False


def test_top_up_starts_a_new_segment():
    declining = [round(1.8 - index * 0.02, 2) for index in range(20)]
    trend = _pressure_trend(declining + [1.8, 1.8, 1.8])

    assert trend.top_ups == 1
    assert trend.days == 3
    assert trend.is_problem is False


def test_two_top_ups_are_a_problem():
    trend = _pressure_trend([1.5] * 5 + [1.8] * 5 + [1.5] * 5 + [1.8] * 5)

    assert trend.top_ups == 2
    assert trend.is_problem


def test_old_top_ups_no_longer_count():
    days = _pressure_days([1.5, 1.8, 1.5, 1.8])
    days = [BoilerHealthDay(date=(TODAY - timedelta(days=200 + index)).isoformat(), cold_pressure=day.cold_pressure) for index, day in enumerate(days)]

    assert BoilerTrendEvaluator.pressure(days, TODAY.toordinal()).top_ups == 0


def _trends_entity_id(hass, entry):
    return er.async_get(hass).async_get_entity_id("binary_sensor", "sat", f"{entry.entry_id}-boiler-trends")


SETUP = pytest.mark.parametrize(("domains", "data", "options", "config"), [([], {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, {}, {})])


@SETUP
async def test_entity_raises_and_clears_the_pressure_issue(hass, coordinator, heating_control, entry, domains, data, options, config):
    monitor = heating_control.boiler_health
    today = dt_util.now().date()
    issue_id = f"boiler_pressure_trend_{entry.entry_id}"

    monitor._days = deque(
        BoilerHealthDay(date=(today - timedelta(days=20 - index)).isoformat(), cold_pressure=round(1.8 - index * 0.02, 2))
        for index in range(20)
    )
    monitor._trends = None

    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_trends_entity_id(hass, entry))
    assert state.state == "on"
    assert state.attributes["pressure_top_ups"] == 0
    assert state.attributes["pressure_loss_bar"] > 0.3
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    monitor._days = deque()
    monitor._trends = None

    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(_trends_entity_id(hass, entry)).state == "off"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@SETUP
async def test_entity_stays_off_without_pressure(hass, coordinator, entry, domains, data, options, config):
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_trends_entity_id(hass, entry))
    assert state.state == "off"
    assert state.attributes["pressure_trend_days"] == 0


def _heating_day(age: int, cycles: int = 10, on_seconds: float = 900.0, outside: float = 5.0, delta: float = 8.0) -> BoilerHealthDay:
    return BoilerHealthDay(
        date=(TODAY - timedelta(days=age)).isoformat(),
        cycle_count=cycles,
        median_on_seconds=on_seconds,
        flow_return_delta=delta,
        outside_temperature=outside,
    )


def _baseline_days(**kwargs) -> list[BoilerHealthDay]:
    return [_heating_day(age, **kwargs) for age in range(30, 8, -1)]


def _recent_days(**kwargs) -> list[BoilerHealthDay]:
    return [_heating_day(age, **kwargs) for age in range(7, 0, -1)]


def _trends(days: list[BoilerHealthDay]):
    return BoilerTrendEvaluator.evaluate(days, TODAY.toordinal())


def test_short_cycling_drift_is_a_problem():
    trends = _trends(_baseline_days() + _recent_days(cycles=20, on_seconds=400.0))

    assert trends.cycling.is_problem
    assert trends.is_problem
    assert trends.cycling.recent_cycles == 20
    assert trends.cycling.baseline_cycles == 10


def test_short_cycling_is_compared_with_similar_weather_only():
    trends = _trends(_baseline_days(outside=-5.0) + _recent_days(cycles=20, on_seconds=400.0, outside=12.0))

    assert trends.cycling.baseline_days == 0
    assert trends.cycling.is_problem is False


def test_on_time_above_target_is_not_short_cycling():
    trends = _trends(_baseline_days(on_seconds=3000.0) + _recent_days(cycles=20, on_seconds=1500.0))

    assert trends.cycling.is_problem is False


def test_more_cycles_with_stable_on_time_is_not_short_cycling():
    trends = _trends(_baseline_days(on_seconds=400.0) + _recent_days(cycles=20, on_seconds=400.0))

    assert trends.cycling.is_problem is False


def test_too_few_recent_heating_days_gives_no_trend():
    trends = _trends(_baseline_days() + _recent_days(cycles=20, on_seconds=400.0)[-4:])

    assert trends.cycling.recent_cycles is None
    assert trends.is_problem is False


def test_flow_return_drift_is_reported_without_being_a_problem():
    trends = _trends(_baseline_days() + _recent_days(delta=12.0))

    assert trends.flow_return.is_drifting
    assert trends.flow_return.recent_delta == 12.0
    assert trends.is_problem is False


def test_small_flow_return_change_is_not_drift():
    assert _trends(_baseline_days() + _recent_days(delta=9.0)).flow_return.is_drifting is False
    assert _trends(_baseline_days(delta=20.0) + _recent_days(delta=23.0)).flow_return.is_drifting is False


@SETUP
async def test_entity_raises_and_clears_the_cycling_issue(hass, coordinator, heating_control, entry, domains, data, options, config):
    monitor = heating_control.boiler_health
    today = dt_util.now().date()
    issue_id = f"boiler_short_cycling_trend_{entry.entry_id}"

    def shifted(day: BoilerHealthDay) -> BoilerHealthDay:
        age = TODAY.toordinal() - day.ordinal
        return replace(day, date=(today - timedelta(days=age)).isoformat())

    monitor._days = deque(shifted(day) for day in _baseline_days() + _recent_days(cycles=20, on_seconds=400.0))
    monitor._trends = None

    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_trends_entity_id(hass, entry))
    assert state.state == "on"
    assert state.attributes["cycling_recent_cycles"] == 20
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    monitor._days = deque()
    monitor._trends = None

    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@SETUP
async def test_diagnostics_include_boiler_trends(hass, heating_control, entry, domains, data, options, config):
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["heating_control"]["boiler_health"]["days"] == 0
    assert diagnostics["heating_control"]["boiler_health"]["problem"] is False
    assert diagnostics["heating_control"]["boiler_health"]["trends"]["pressure"]["top_ups"] == 0


@pytest.mark.parametrize("key", ["boiler_pressure_trend", "boiler_short_cycling_trend"])
def test_issues_are_translated_in_every_language(key):
    translations = Path(custom_components.sat.__file__).parent / "translations"
    placeholders = None

    for path in sorted(translations.glob("*.json")):
        issue = json.loads(path.read_text(encoding="utf-8"))["issues"][key]
        found = {word for word in issue["description"].replace("}", "{").split("{")[1::2]}

        assert issue["title"]
        assert placeholders is None or found == placeholders
        placeholders = found
