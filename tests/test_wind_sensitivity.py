"""Unit tests for the wind sensitivity fit."""

from datetime import date, timedelta

import pytest

from custom_components.sat.heating_curve import HeatingCurve, HeatingCurveAdviceSample, HeatingCurveAdvisor, RECOMMENDATION_MINIMUM_DAILY_SAMPLES, RECOMMENDATION_SAMPLE_INTERVAL_SECONDS, RECOMMENDATION_WINDOW_SECONDS
from custom_components.sat.helpers import timestamp
from custom_components.sat.wind import WindReading
from custom_components.sat.wind_sensitivity import (
    WIND_SENSITIVITY_MAXIMUM,
    WIND_SENSITIVITY_RETENTION_DAYS,
    WindDay,
    WindSensitivity,
    WindSensitivityEvaluator,
    WindSensitivityMonitor,
)

TODAY = date(2026, 11, 20)
SPEEDS = [2.0, 5.0, 10.0, 18.0, 30.0, 42.0, 55.0, 60.0]


def _days(speeds: list[float], calm: float = 2.3, sensitivity: float = 0.005) -> list[WindDay]:
    return [
        WindDay(date=(TODAY - timedelta(days=index + 1)).isoformat(), wind_speed=speed, coefficient=calm * (1 + sensitivity * speed), sample_count=100)
        for index, speed in enumerate(speeds)
    ]


def test_fit_recovers_the_calm_coefficient_and_sensitivity():
    fit = WindSensitivityEvaluator.fit(_days(SPEEDS))

    assert fit.calm_coefficient == pytest.approx(2.3, abs=0.01)
    assert fit.sensitivity == pytest.approx(0.005, abs=0.0001)
    assert fit.percent_per_10_kmh == pytest.approx(5.0, abs=0.1)


def test_one_odd_day_does_not_move_the_fit():
    days = _days(SPEEDS)
    days[3] = WindDay(date=days[3].date, wind_speed=days[3].wind_speed, coefficient=4.5, sample_count=100)

    assert WindSensitivityEvaluator.fit(days).sensitivity == pytest.approx(0.005, abs=0.0005)


def test_too_few_days_give_no_fit():
    fit = WindSensitivityEvaluator.fit(_days(SPEEDS[:5]))

    assert fit == WindSensitivity(day_count=5, wind_spread=fit.wind_spread)
    assert fit.percent_per_10_kmh is None


def test_calm_weeks_give_no_fit():
    fit = WindSensitivityEvaluator.fit(_days([2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0]))

    assert fit.sensitivity is None
    assert fit.wind_spread < 15.0


def test_spread_compares_the_windiest_third_with_the_calmest_third():
    assert WindSensitivityEvaluator.spread(_days([1.0, 2.0, 10.0, 20.0, 40.0, 41.0])) == pytest.approx(39.0)
    assert WindSensitivityEvaluator.spread(_days([5.0, 9.0])) is None


def test_less_heat_in_wind_is_reported_as_no_sensitivity():
    assert WindSensitivityEvaluator.fit(_days(SPEEDS, sensitivity=-0.004)).sensitivity == 0.0


def test_implausible_sensitivity_is_capped():
    assert WindSensitivityEvaluator.fit(_days(SPEEDS, sensitivity=0.05)).sensitivity == WIND_SENSITIVITY_MAXIMUM


def test_monitor_keeps_the_most_complete_estimate_of_a_day():
    monitor = WindSensitivityMonitor()
    first = WindDay(date="2026-11-19", wind_speed=20.0, coefficient=2.5, sample_count=120)
    monitor.update([first], TODAY)

    monitor.update([WindDay(date="2026-11-19", wind_speed=25.0, coefficient=2.1, sample_count=70)], TODAY)
    assert monitor.days == [first]

    complete = WindDay(date="2026-11-19", wind_speed=22.0, coefficient=2.6, sample_count=150)
    monitor.update([complete], TODAY)
    assert monitor.days == [complete]


def test_monitor_drops_days_past_retention():
    monitor = WindSensitivityMonitor()
    old = WindDay(date=(TODAY - timedelta(days=WIND_SENSITIVITY_RETENTION_DAYS + 1)).isoformat(), wind_speed=10.0, coefficient=2.4, sample_count=100)
    recent = WindDay(date="2026-11-19", wind_speed=10.0, coefficient=2.4, sample_count=100)

    monitor.update([old, recent], TODAY)

    assert monitor.days == [recent]


def test_monitor_refits_when_days_arrive():
    monitor = WindSensitivityMonitor()
    assert monitor.sensitivity.day_count == 0

    monitor.update(_days(SPEEDS), TODAY)

    assert monitor.sensitivity.sensitivity == pytest.approx(0.005, abs=0.0001)


async def test_monitor_persists_and_restores_its_days(hass):
    monitor = WindSensitivityMonitor()
    await monitor.async_added_to_hass(hass, "entry")
    monitor.update(_days(SPEEDS), TODAY)
    await monitor._store.async_save(monitor._data_to_save())

    restored = WindSensitivityMonitor()
    await restored.async_added_to_hass(hass, "entry")

    assert restored.days == monitor.days
    assert restored.sensitivity == monitor.sensitivity


def test_advisor_days_carry_the_absolute_coefficient_and_wind():
    advisor = HeatingCurveAdvisor(coefficient=2.0)
    shape = HeatingCurve.calculate(21.0, 5.0)
    start = timestamp() - RECOMMENDATION_WINDOW_SECONDS + 2 * 24 * 60 * 60

    for index in range(RECOMMENDATION_MINIMUM_DAILY_SAMPLES):
        advisor.record(HeatingCurveAdviceSample(
            shape=shape,
            correction=shape / 4,
            timestamp=start + index * RECOMMENDATION_SAMPLE_INTERVAL_SECONDS,
            wind=WindReading(speed=30.0, entity_id="sensor.wind"),
        ))

    [day] = advisor.advice.wind_days

    assert day.coefficient == pytest.approx(3.0)
    assert day.wind_speed == 30.0
    assert day.sample_count == RECOMMENDATION_MINIMUM_DAILY_SAMPLES


def test_advisor_days_without_wind_are_left_out():
    advisor = HeatingCurveAdvisor(coefficient=2.0)
    shape = HeatingCurve.calculate(21.0, 5.0)
    start = timestamp() - RECOMMENDATION_WINDOW_SECONDS + 2 * 24 * 60 * 60

    for index in range(RECOMMENDATION_MINIMUM_DAILY_SAMPLES):
        advisor.record(HeatingCurveAdviceSample(shape=shape, correction=1.0, timestamp=start + index * RECOMMENDATION_SAMPLE_INTERVAL_SECONDS))

    assert advisor.advice.wind_days == []
