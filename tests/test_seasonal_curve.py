"""Tests for the seasonal heating curve fit."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.sat.const import DOMAIN
from custom_components.sat.heating_curve import HeatingCurveAdviceSample, HeatingCurveAdvisor, RECOMMENDATION_MINIMUM_SHAPE
from custom_components.sat.seasonal_curve import STARTUP_DELAY_SECONDS, SeasonalCurveEvaluator, SeasonalCurveHour, SeasonalCurveMonitor

HOUR_SECONDS = 3600.0


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations():
    """Replace the shared fixture, which sets up hass before recorder_mock is allowed to."""
    yield


START = dt_util.utc_from_timestamp(1_790_000_000.0).replace(minute=0, second=0, microsecond=0).timestamp()


def _hours(count: int, coefficient: float = 2.0, outside: float = 5.0, offset: int = 0, spacing: float = 6 * HOUR_SECONDS) -> list[SeasonalCurveHour]:
    return [
        SeasonalCurveHour(start=START + (offset + index) * spacing, coefficient=coefficient, outside_temperature=outside)
        for index in range(count)
    ]


def test_implied_coefficient_keeps_its_meaning_after_a_retune():
    shape = 20.0

    before = HeatingCurveAdvisor(1.5).implied_coefficient(HeatingCurveAdviceSample(shape=shape, timestamp=0.0, correction=2.5))
    after = HeatingCurveAdvisor(2.0).implied_coefficient(HeatingCurveAdviceSample(shape=shape, timestamp=0.0, correction=0.0))

    assert before == 2.0
    assert after == 2.0


def test_implied_coefficient_needs_enough_curve_to_measure():
    advisor = HeatingCurveAdvisor(1.5)
    sample = HeatingCurveAdviceSample(shape=RECOMMENDATION_MINIMUM_SHAPE - 0.1, timestamp=0.0, correction=1.0)

    assert advisor.implied_coefficient(sample) is None
    assert advisor.implied_coefficient(None) is None


def test_known_coefficient_is_recovered():
    fit = SeasonalCurveEvaluator.fit(_hours(60, coefficient=2.3))

    assert fit.coefficient == 2.3
    assert fit.hours == 60
    assert fit.days >= 5


def test_cold_hours_outweigh_mild_hours():
    cold = _hours(30, coefficient=2.0, outside=-5.0)
    mild = _hours(30, coefficient=3.0, outside=15.0, offset=30)

    assert SeasonalCurveEvaluator.fit(cold + mild).coefficient == 2.0


def test_too_few_hours_give_no_fit():
    fit = SeasonalCurveEvaluator.fit(_hours(47))

    assert fit.coefficient is None
    assert fit.hours == 47


def test_too_few_days_give_no_fit():
    fit = SeasonalCurveEvaluator.fit(_hours(60, spacing=HOUR_SECONDS))

    assert fit.days < 5
    assert fit.coefficient is None


def test_unpaired_hours_are_dropped():
    hours = SeasonalCurveEvaluator.pair(
        [{"start": 1.0, "mean": 2.0}, {"start": 2.0, "mean": 2.1}, {"start": 3.0, "mean": None}, {"start": 4.0, "mean": 2.2}],
        [{"start": 1.0, "mean": 5.0}, {"start": 3.0, "mean": 6.0}, {"start": 4.0, "mean": None}],
    )

    assert hours == [SeasonalCurveHour(start=1.0, coefficient=2.0, outside_temperature=5.0)]


def test_mismatch_is_flagged_with_enough_data_on_both_sides():
    fit = SeasonalCurveEvaluator.fit(_hours(30, coefficient=2.5, outside=-5.0) + _hours(30, coefficient=1.5, outside=12.0, offset=30))

    assert fit.cold_coefficient == 2.5
    assert fit.mild_coefficient == 1.5
    assert fit.is_mismatch


def test_small_difference_is_not_a_mismatch():
    fit = SeasonalCurveEvaluator.fit(_hours(30, coefficient=2.0, outside=-5.0) + _hours(30, coefficient=1.8, outside=12.0, offset=30))

    assert fit.is_mismatch is False


def test_mismatch_needs_data_on_both_sides():
    fit = SeasonalCurveEvaluator.fit(_hours(50, coefficient=2.5, outside=-5.0) + _hours(10, coefficient=1.0, outside=12.0, offset=50))

    assert fit.mild_coefficient is None
    assert fit.is_mismatch is False


def test_bins_need_enough_hours():
    fit = SeasonalCurveEvaluator.fit(_hours(40, coefficient=2.0, outside=4.0) + _hours(11, coefficient=3.0, outside=-8.0, offset=40))

    assert [(item.minimum_temperature, item.hours, item.coefficient) for item in fit.bins] == [(3, 40, 2.0)]


async def test_refresh_without_recorder_leaves_an_empty_fit(hass):
    monitor = SeasonalCurveMonitor("entry")
    monitor._hass = hass

    await monitor.async_refresh()

    assert monitor.fit.coefficient is None


async def test_refresh_keeps_the_previous_fit_when_the_query_fails(hass, monkeypatch):
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, "entry-implied-curve-coefficient")
    registry.async_get_or_create("sensor", DOMAIN, "entry-outside-temperature")
    hass.config.components.add("recorder")

    monitor = SeasonalCurveMonitor("entry")
    monitor._hass = hass
    monitor._fit = SeasonalCurveEvaluator.fit(_hours(60, coefficient=2.3))
    monkeypatch.setattr(monitor, "_async_load_statistics", AsyncMock(side_effect=RuntimeError("database is gone")))

    await monitor.async_refresh()

    assert monitor.fit.coefficient == 2.3


async def test_fit_refreshes_every_hour(hass, monkeypatch):
    monitor = SeasonalCurveMonitor("entry")
    refresh = AsyncMock()
    monkeypatch.setattr(monitor, "async_refresh", refresh)

    await monitor.async_added_to_hass(hass)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=STARTUP_DELAY_SECONDS + 1))
    await hass.async_block_till_done()
    assert refresh.await_count == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=1, seconds=1))
    await hass.async_block_till_done()
    assert refresh.await_count == 2

    await monitor.async_will_remove_from_hass()


def _import(hass, statistic_id: str, unit, values: list[float]) -> None:
    first = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=len(values) * 3 + 1)

    async_import_statistics(
        hass,
        {
            "has_sum": False,
            "mean_type": StatisticMeanType.ARITHMETIC,
            "name": None,
            "source": "recorder",
            "statistic_id": statistic_id,
            "unit_class": TemperatureConverter.UNIT_CLASS if unit else None,
            "unit_of_measurement": unit,
        },
        [{"start": first + timedelta(hours=index * 3), "mean": value, "min": value, "max": value} for index, value in enumerate(values)],
    )


async def test_refresh_fits_from_real_statistics(recorder_mock, hass):
    registry = er.async_get(hass)
    coefficient = registry.async_get_or_create("sensor", DOMAIN, "entry-implied-curve-coefficient", suggested_object_id="implied")
    temperature = registry.async_get_or_create("sensor", DOMAIN, "entry-outside-temperature", suggested_object_id="outside")

    _import(hass, coefficient.entity_id, None, [2.25] * 60)
    _import(hass, temperature.entity_id, "°F", [41.0] * 60)
    await async_wait_recording_done(hass)

    monitor = SeasonalCurveMonitor("entry")
    monitor._hass = hass
    await monitor.async_refresh()

    assert monitor.fit.hours == 60
    assert monitor.fit.coefficient == 2.25
    assert [item.minimum_temperature for item in monitor.fit.bins] == [3]
