"""Unit tests for the heating curve advisor."""

import asyncio

from custom_components.sat.heating_curve import (
    HeatingCurve,
    HeatingCurveAdviceSample,
    HeatingCurveAdvisor,
    RECOMMENDATION_COEFFICIENT_SETTLE_SECONDS,
    RECOMMENDATION_MINIMUM_DAILY_SAMPLES,
    RECOMMENDATION_MINIMUM_DAYS,
    RECOMMENDATION_MINIMUM_SAMPLES,
    RECOMMENDATION_SAMPLE_INTERVAL_SECONDS,
    RECOMMENDATION_WINDOW_SECONDS,
    STORAGE_SAVE_INTERVAL_SECONDS,
)
from custom_components.sat.helpers import timestamp, weighted_median
from custom_components.sat.types import HeatingCurveConfidence, HeatingCurveRecommendation, HeatingSystem
from custom_components.sat.wind import WindHistory, WindReading

DAY_SECONDS = 24 * 60 * 60
SHAPE = HeatingCurve.calculate(21.0, 5.0)
START = timestamp() - RECOMMENDATION_WINDOW_SECONDS + 2 * DAY_SECONDS


def _fill(advisor: HeatingCurveAdvisor, correction: float, *, days: int = RECOMMENDATION_MINIMUM_DAYS, count: int = RECOMMENDATION_MINIMUM_SAMPLES, shape: float = SHAPE, start_day: int = 0) -> None:
    for day in range(start_day, start_day + days):
        for index in range(count):
            advisor.record(HeatingCurveAdviceSample(
                shape=shape,
                timestamp=START + day * DAY_SECONDS + index * RECOMMENDATION_SAMPLE_INTERVAL_SECONDS,
                correction=correction,
            ))


def test_weighted_median_prefers_heavy_values():
    assert weighted_median([1.0, 2.0, 10.0], [1.0, 1.0, 5.0]) == 10.0
    assert weighted_median([1.0, 2.0, 3.0], [1.0, 1.0, 1.0]) == 2.0
    assert weighted_median([1.0, 2.0], [0.0, 0.0]) is None


def test_heating_curve_exposes_shape():
    curve = HeatingCurve(heating_system=HeatingSystem.RADIATORS, coefficient=1.8)
    curve.update(21.0, 5.0)

    assert curve.shape == SHAPE
    assert curve.value == round(27.2 + (1.8 / 4) * SHAPE, 1)

    curve.reset()

    assert curve.shape is None


def test_insufficient_samples_without_any_qualifying_day():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, days=1, count=RECOMMENDATION_MINIMUM_DAILY_SAMPLES - 1)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.INSUFFICIENT_SAMPLES
    assert advice.confidence is None
    assert advice.sample_days == 0
    assert advice.estimated_coefficient is None
    assert advice.suggested_coefficient is None


def test_single_day_shows_estimate_but_no_recommendation():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, days=1)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.INSUFFICIENT_SAMPLES
    assert advice.sample_days == 1
    assert advice.estimated_coefficient == round(1.8 + 4 * 5.0 / SHAPE, 1)
    assert advice.suggested_coefficient is None


def test_sustained_positive_correction_recommends_increase():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.INCREASE
    assert advice.confidence is HeatingCurveConfidence.MEDIUM
    assert advice.median_correction == 5.0
    assert advice.estimated_coefficient == round(1.8 + 4 * 5.0 / SHAPE, 1)
    assert advice.suggested_coefficient == 2.7
    assert list(advice.daily_coefficients.values()) == [advice.estimated_coefficient, advice.estimated_coefficient]


def test_sustained_negative_correction_recommends_decrease():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, -2.0)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.DECREASE
    assert advice.suggested_coefficient < 1.8


def test_small_correction_holds():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 0.3)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.HOLD
    assert advice.suggested_coefficient == 1.8


def test_sample_implies_coefficient_delta():
    sample = HeatingCurveAdviceSample(shape=SHAPE, timestamp=0.0, correction=5.0)

    assert sample.coefficient_delta == 4 * 5.0 / SHAPE


def test_large_estimate_is_suggested_in_bounded_steps():
    advisor = HeatingCurveAdvisor(coefficient=1.4)
    _fill(advisor, 6.3, shape=4.35)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.INCREASE
    assert advice.estimated_coefficient == round(1.4 + 4 * 6.3 / 4.35, 1)
    assert advice.suggested_coefficient == 2.1


def test_small_coefficient_keeps_minimum_step():
    advisor = HeatingCurveAdvisor(coefficient=0.4)
    _fill(advisor, 6.3)

    assert advisor.advice.suggested_coefficient == 0.9


def test_one_stormy_day_does_not_dominate():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 0.1, days=3)
    _fill(advisor, 8.0, days=1, start_day=3)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.HOLD
    assert advice.confidence is HeatingCurveConfidence.MEDIUM
    assert advice.sample_days == 4


def test_consistent_days_give_high_confidence():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, days=3)

    assert advisor.advice.confidence is HeatingCurveConfidence.HIGH


def test_disagreeing_days_give_low_confidence():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, days=1)
    _fill(advisor, -5.0, days=1, start_day=1)
    _fill(advisor, 5.0, days=1, start_day=2)
    _fill(advisor, -5.0, days=1, start_day=3)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.HOLD
    assert advice.confidence is HeatingCurveConfidence.LOW


def test_cold_weather_samples_outweigh_mild_weather_samples():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    cold_shape = HeatingCurve.calculate(21.0, -5.0)

    for day in range(RECOMMENDATION_MINIMUM_DAYS):
        for index in range(RECOMMENDATION_MINIMUM_SAMPLES // 2):
            cold = index % 2 == 0
            advisor.record(HeatingCurveAdviceSample(
                shape=cold_shape if cold else SHAPE,
                timestamp=START + day * DAY_SECONDS + index * RECOMMENDATION_SAMPLE_INTERVAL_SECONDS,
                correction=5.0 if cold else -5.0,
            ))

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.INCREASE
    assert advice.estimated_coefficient == round(1.8 + 4 * 5.0 / cold_shape, 1)


def test_mild_weather_samples_are_skipped():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, shape=HeatingCurve.calculate(20.0, 19.5))

    advice = advisor.advice

    assert advice.sample_count == 0
    assert advice.recommendation is HeatingCurveRecommendation.INSUFFICIENT_SAMPLES


def test_samples_are_throttled_to_the_sample_interval():
    advisor = HeatingCurveAdvisor(coefficient=1.8)

    for index in range(10):
        advisor.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=START + index * 10.0, correction=5.0))

    assert advisor.advice.sample_count == 2


def test_window_prunes_old_samples():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, days=1)

    advisor.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=START + RECOMMENDATION_WINDOW_SECONDS * 2, correction=5.0))

    assert advisor.advice.sample_count == 1


def test_advice_is_cached_until_the_next_sample():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0)

    first = advisor.advice

    assert advisor.advice is first

    advisor.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=START + RECOMMENDATION_WINDOW_SECONDS, correction=5.0))

    assert advisor.advice is not first


async def test_advisor_persists_and_restores_samples(hass):
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    await advisor.async_added_to_hass(hass, "entry1")
    _fill(advisor, 5.0)
    await advisor._store.async_save(advisor._data_to_save())

    restored = HeatingCurveAdvisor(coefficient=1.8)
    await restored.async_added_to_hass(hass, "entry1")

    assert restored.advice.sample_count == advisor.advice.sample_count
    assert restored.advice.recommendation is advisor.advice.recommendation


async def test_advisor_persists_and_restores_the_wind_of_each_sample(hass):
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    await advisor.async_added_to_hass(hass, "entry4")
    advisor.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=START, correction=5.0, wind=WindReading(speed=32.5, entity_id="weather.home")))
    advisor.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=START + RECOMMENDATION_SAMPLE_INTERVAL_SECONDS, correction=5.0))
    await advisor._store.async_save(advisor._data_to_save())

    restored = HeatingCurveAdvisor(coefficient=1.8)
    await restored.async_added_to_hass(hass, "entry4")

    assert [sample.wind for sample in restored._samples] == [WindReading(speed=32.5, entity_id="weather.home"), None]


def test_daily_wind_speed_is_the_median_of_the_samples_with_wind():
    advisor = HeatingCurveAdvisor(coefficient=1.8)

    for day, speeds in enumerate(([10.0, 20.0, 60.0], [None, None, None])):
        for index in range(RECOMMENDATION_MINIMUM_DAILY_SAMPLES):
            speed = speeds[index % len(speeds)]
            advisor.record(HeatingCurveAdviceSample(
                shape=SHAPE,
                correction=5.0,
                timestamp=START + day * DAY_SECONDS + index * RECOMMENDATION_SAMPLE_INTERVAL_SECONDS,
                wind=WindReading(speed=speed, entity_id="weather.home") if speed is not None else None,
            ))

    assert list(advisor.advice.daily_wind_speeds.values()) == [20.0]
    assert len(advisor.advice.daily_coefficients) == 2


def test_backfill_gives_samples_without_wind_the_mean_of_their_hour():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    known = WindReading(speed=5.0, entity_id="sensor.wind")
    advisor.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=START, correction=5.0))
    advisor.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=START + 3600.0, correction=5.0, wind=known))
    advisor.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=START + 7200.0, correction=5.0))

    assert advisor.first_timestamp_without_wind == START

    hourly = {WindHistory.hour(START): WindReading(speed=30.0, entity_id="sensor.sat_wind_speed"), WindHistory.hour(START + 3600.0): WindReading(speed=99.0, entity_id="sensor.sat_wind_speed")}

    assert advisor.backfill_wind(hourly) == 1
    assert [sample.wind for sample in advisor._samples] == [WindReading(speed=30.0, entity_id="sensor.sat_wind_speed"), known, None]
    assert advisor.first_timestamp_without_wind == START + 7200.0


def test_backfill_refreshes_the_daily_wind_speeds():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, days=1, count=RECOMMENDATION_MINIMUM_DAILY_SAMPLES)

    assert advisor.advice.daily_wind_speeds == {}

    advisor.backfill_wind({WindHistory.hour(sample.timestamp): WindReading(speed=12.0, entity_id="sensor.wind") for sample in advisor._samples})

    assert list(advisor.advice.daily_wind_speeds.values()) == [12.0]


def test_backfill_without_history_changes_nothing():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, days=1, count=3)

    assert advisor.backfill_wind({}) == 0
    assert advisor.first_timestamp_without_wind == START


async def test_advisor_saves_periodically_while_recording(hass):
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    await advisor.async_added_to_hass(hass, "entry3")

    for index in range(int(STORAGE_SAVE_INTERVAL_SECONDS // RECOMMENDATION_SAMPLE_INTERVAL_SECONDS) + 2):
        advisor.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=START + index * RECOMMENDATION_SAMPLE_INTERVAL_SECONDS, correction=5.0))

    await asyncio.sleep(0.1)
    await hass.async_block_till_done()

    restored = HeatingCurveAdvisor(coefficient=1.8)
    await restored.async_added_to_hass(hass, "entry3")

    assert restored.advice.sample_count >= 1


async def test_advisor_discards_samples_for_other_coefficient(hass):
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    await advisor.async_added_to_hass(hass, "entry2")
    _fill(advisor, 5.0)
    await advisor._store.async_save(advisor._data_to_save())

    restored = HeatingCurveAdvisor(coefficient=2.4)
    await restored.async_added_to_hass(hass, "entry2")

    assert restored.advice.sample_count == 0


async def test_samples_wait_for_the_house_to_settle_after_a_coefficient_change(hass):
    advisor = HeatingCurveAdvisor(coefficient=3.0)
    await advisor.async_added_to_hass(hass, "entry5")
    _fill(advisor, 5.0, days=1, count=3)
    await advisor._store.async_save(advisor._data_to_save())

    changed = HeatingCurveAdvisor(coefficient=2.2)
    await changed.async_added_to_hass(hass, "entry5")
    now = timestamp()

    changed.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=now + RECOMMENDATION_COEFFICIENT_SETTLE_SECONDS - 60.0, correction=-5.0))
    assert changed.advice.sample_count == 0

    changed.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=now + RECOMMENDATION_COEFFICIENT_SETTLE_SECONDS + 60.0, correction=-5.0))
    assert changed.advice.sample_count == 1


async def test_implied_coefficient_is_unknown_while_the_house_settles(hass):
    advisor = HeatingCurveAdvisor(coefficient=3.0)
    await advisor.async_added_to_hass(hass, "entry8")
    await advisor._store.async_save(advisor._data_to_save())

    changed = HeatingCurveAdvisor(coefficient=2.2)
    await changed.async_added_to_hass(hass, "entry8")
    now = timestamp()

    assert changed.implied_coefficient(HeatingCurveAdviceSample(shape=SHAPE, timestamp=now + 60.0, correction=0.0)) is None
    assert changed.implied_coefficient(HeatingCurveAdviceSample(shape=SHAPE, timestamp=now + RECOMMENDATION_COEFFICIENT_SETTLE_SECONDS + 60.0, correction=0.0)) == 2.2


async def test_settling_survives_a_restart(hass):
    advisor = HeatingCurveAdvisor(coefficient=3.0)
    await advisor.async_added_to_hass(hass, "entry6")
    await advisor._store.async_save(advisor._data_to_save())

    changed = HeatingCurveAdvisor(coefficient=2.2)
    await changed.async_added_to_hass(hass, "entry6")
    await changed._store.async_save(changed._data_to_save())

    restarted = HeatingCurveAdvisor(coefficient=2.2)
    await restarted.async_added_to_hass(hass, "entry6")
    restarted.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=timestamp() + 60.0, correction=-5.0))

    assert restarted.advice.sample_count == 0


async def test_first_install_samples_without_settling(hass):
    advisor = HeatingCurveAdvisor(coefficient=2.2)
    await advisor.async_added_to_hass(hass, "entry7")
    advisor.record(HeatingCurveAdviceSample(shape=SHAPE, timestamp=timestamp(), correction=1.0))

    assert advisor.advice.sample_count == 1


def test_mild_weather_never_earns_more_than_low_confidence():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, days=3, shape=4.35)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.INCREASE
    assert advice.confidence is HeatingCurveConfidence.LOW
