"""Tests for the seasonal efficiency report."""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter, VolumeConverter
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.sat.const import DOMAIN
from custom_components.sat.efficiency import EfficiencyDay, EfficiencyEvaluator, EfficiencyMonitor, EfficiencyReport, EfficiencyFit, GasHistory, SOURCE_ESTIMATE, SOURCE_METER

TODAY = date(2026, 11, 15)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations():
    """Replace the shared fixture, which sets up hass before recorder_mock is allowed to."""
    yield


def _row(day: date, **values) -> dict:
    moment = datetime.combine(day, time(0), tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return {"start": moment.timestamp(), **values}


def _days(count: int, slope: float = 0.5, baseload: float = 1.0, end: date = TODAY) -> list[EfficiencyDay]:
    return [
        EfficiencyDay(date=end - timedelta(days=index + 1), degree_days=(degree_days := 5.0 + index % 10), gas=baseload + slope * degree_days)
        for index in range(count)
    ]


def _history(days: list[EfficiencyDay]) -> tuple:
    gas = [_row(day.date, change=day.gas) for day in days]
    temperatures = {day.date: 18.0 - day.degree_days for day in days}

    return GasHistory(source=SOURCE_METER, unit="m³", rows=gas), temperatures


def test_known_slope_and_baseload_are_recovered():
    fit = EfficiencyEvaluator.fit(_days(30))

    assert fit.slope == 0.5
    assert fit.baseload == 1.0
    assert fit.r_squared == 1.0
    assert fit.days == 30


def test_flat_summer_days_give_no_fit():
    summer = [EfficiencyDay(date=TODAY - timedelta(days=index + 1), gas=1.0, degree_days=0.0) for index in range(30)]

    assert EfficiencyEvaluator.fit(summer) is None


def test_too_few_days_give_no_fit():
    assert EfficiencyEvaluator.fit(_days(20)) is None


def test_too_few_degree_days_give_no_fit():
    days = [EfficiencyDay(date=TODAY - timedelta(days=index + 1), gas=1.0 + index % 2, degree_days=1.0 * (index % 2)) for index in range(30)]

    assert EfficiencyEvaluator.fit(days) is None


def test_gas_falling_with_cold_gives_no_fit():
    assert EfficiencyEvaluator.fit(_days(30, slope=-0.1, baseload=5.0)) is None


def test_negative_change_and_missing_days_are_dropped():
    temperatures = {TODAY - timedelta(days=1): 8.0, TODAY - timedelta(days=2): 8.0, TODAY - timedelta(days=3): 8.0}
    rows = [
        _row(TODAY - timedelta(days=1), change=2.0),
        _row(TODAY - timedelta(days=2), change=-500.0),
        _row(TODAY - timedelta(days=3), change=None),
        _row(TODAY - timedelta(days=4), change=2.0),
    ]

    assert [day.date for day in EfficiencyEvaluator.days(rows, temperatures)] == [TODAY - timedelta(days=1)]


def test_today_is_not_part_of_the_fit():
    days = _days(30) + [EfficiencyDay(date=TODAY, gas=500.0, degree_days=5.0)]
    gas, temperatures = _history(days)

    assert EfficiencyEvaluator.report(gas, temperatures, TODAY).current.slope == 0.5


def test_rise_compared_with_last_year_is_reported():
    gas, temperatures = _history(_days(30, slope=0.55) + _days(30, slope=0.5, end=date(2025, 11, 15)))

    report = EfficiencyEvaluator.report(gas, temperatures, TODAY)

    assert report.current.slope == 0.55
    assert report.previous.slope == 0.5
    assert report.change_percentage == 10.0


def test_days_outside_the_same_calendar_window_are_not_compared():
    gas, temperatures = _history(_days(30) + _days(30, end=date(2025, 7, 1)))

    report = EfficiencyEvaluator.report(gas, temperatures, TODAY)

    assert report.previous is None
    assert report.change_percentage is None


def test_leap_day_compares_with_the_end_of_february():
    assert EfficiencyEvaluator._year_before(date(2028, 2, 29)) == date(2027, 2, 28)


def test_gas_estimate_ratio_is_recovered():
    days = [TODAY - timedelta(days=index + 1) for index in range(14)]

    ratio = EfficiencyEvaluator.gas_estimate_ratio([_row(day, change=3.0) for day in days], [_row(day, change=2.0) for day in days], TODAY)

    assert ratio == 1.5


def test_gas_estimate_ratio_needs_enough_shared_days():
    days = [TODAY - timedelta(days=index + 1) for index in range(13)]

    assert EfficiencyEvaluator.gas_estimate_ratio([_row(day, change=3.0) for day in days], [_row(day, change=2.0) for day in days], TODAY) is None


def test_earlier_temperature_sources_take_precedence_and_gaps_are_filled():
    first, second = TODAY - timedelta(days=1), TODAY - timedelta(days=2)

    merged = EfficiencyEvaluator.temperatures([[_row(first, mean=5.0)], [_row(first, mean=9.0), _row(second, mean=7.0)]])

    assert merged == {first: 5.0, second: 7.0}


def test_change_needs_both_fits():
    fit = EfficiencyFit(days=30, slope=0.5, baseload=1.0, r_squared=1.0)

    assert EfficiencyReport(current=fit).change_percentage is None
    assert EfficiencyReport(previous=fit).change_percentage is None


async def test_refresh_without_recorder_leaves_an_empty_report(hass):
    monitor = EfficiencyMonitor("entry", [])
    monitor._hass = hass

    await monitor.async_refresh()

    assert monitor.report == EfficiencyReport()


async def test_refresh_keeps_the_previous_report_when_the_query_fails(hass, monkeypatch):
    er.async_get(hass).async_get_or_create("sensor", DOMAIN, "entry-boiler-total-consumption")
    hass.config.components.add("recorder")

    monitor = EfficiencyMonitor("entry", [])
    monitor._hass = hass
    monitor._report = EfficiencyReport(source=SOURCE_METER)
    monkeypatch.setattr(monitor, "_async_load_statistics", AsyncMock(side_effect=RuntimeError("database is gone")))

    await monitor.async_refresh()

    assert monitor.report.source == SOURCE_METER


async def test_no_gas_source_gives_an_empty_report(hass):
    hass.config.components.add("recorder")

    monitor = EfficiencyMonitor("entry", [])
    monitor._hass = hass

    await monitor.async_refresh()

    assert monitor.report == EfficiencyReport()


def _import(hass, statistic_id: str, unit, unit_class, rows: list[dict], has_sum: bool = False) -> None:
    async_import_statistics(
        hass,
        {
            "has_sum": has_sum,
            "mean_type": StatisticMeanType.NONE if has_sum else StatisticMeanType.ARITHMETIC,
            "name": None,
            "source": "recorder",
            "statistic_id": statistic_id,
            "unit_class": unit_class,
            "unit_of_measurement": unit,
        },
        rows,
    )


def _noon(age: int) -> datetime:
    day = dt_util.now().date() - timedelta(days=age)
    return dt_util.as_utc(datetime.combine(day, time(12), tzinfo=dt_util.DEFAULT_TIME_ZONE))


def _import_house(hass, gas_id: str, temperature_id: str, unit: str = "m³", days: int = 40) -> None:
    total, gas_rows, temperature_rows = 0.0, [], []

    for age in range(days, 0, -1):
        outside = 2.0 + age % 10
        total += 1.0 + 0.5 * (18.0 - outside)

        gas_rows.append({"start": _noon(age), "sum": total, "state": total})
        temperature_rows.append({"start": _noon(age), "mean": outside, "min": outside, "max": outside})

    _import(hass, gas_id, unit, VolumeConverter.UNIT_CLASS if unit == "m³" else "energy", gas_rows, has_sum=True)
    _import(hass, temperature_id, "°C", TemperatureConverter.UNIT_CLASS, temperature_rows)


def _energy_manager(gas_id: str, monkeypatch) -> None:
    manager = SimpleNamespace(data={"energy_sources": [{"type": "grid"}, {"type": "gas", "stat_energy_from": gas_id}]})
    monkeypatch.setattr("homeassistant.components.energy.data.async_get_manager", AsyncMock(return_value=manager))


async def test_report_from_real_statistics_with_a_meter(recorder_mock, hass, monkeypatch):
    hass.config.components.add("energy")
    _energy_manager("sensor.gas_meter", monkeypatch)
    _import_house(hass, "sensor.gas_meter", "sensor.outside")
    await async_wait_recording_done(hass)

    monitor = EfficiencyMonitor("entry", ["sensor.outside", "weather.home"])
    monitor._hass = hass
    await monitor.async_refresh()

    report = monitor.report
    assert report.source == SOURCE_METER
    assert report.unit == "m³"
    assert report.current.slope == pytest.approx(0.5, abs=0.01)
    assert report.current.baseload == pytest.approx(1.0, abs=0.1)
    assert report.previous is None


async def test_fallback_to_the_estimate_is_reported(recorder_mock, hass):
    estimate = er.async_get(hass).async_get_or_create("sensor", DOMAIN, "entry-boiler-total-consumption", suggested_object_id="total")
    _import_house(hass, estimate.entity_id, "sensor.outside")
    await async_wait_recording_done(hass)

    monitor = EfficiencyMonitor("entry", ["sensor.outside"])
    monitor._hass = hass
    await monitor.async_refresh()

    assert monitor.report.source == SOURCE_ESTIMATE
    assert monitor.report.current is not None
    assert monitor.report.gas_estimate_ratio is None


async def test_ratio_is_skipped_for_a_meter_in_energy_units(recorder_mock, hass, monkeypatch):
    estimate = er.async_get(hass).async_get_or_create("sensor", DOMAIN, "entry-boiler-total-consumption", suggested_object_id="total")
    hass.config.components.add("energy")
    _energy_manager("sensor.gas_meter", monkeypatch)
    _import_house(hass, "sensor.gas_meter", "sensor.outside", unit="kWh")
    _import_house(hass, estimate.entity_id, "sensor.outside")
    await async_wait_recording_done(hass)

    monitor = EfficiencyMonitor("entry", ["sensor.outside"])
    monitor._hass = hass
    await monitor.async_refresh()

    assert monitor.report.unit == "kWh"
    assert monitor.report.gas_estimate_ratio is None
