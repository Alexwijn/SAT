"""Unit tests for the heating curve advisor."""

from custom_components.sat.heating_curve import (
    HeatingCurve,
    HeatingCurveAdvisor,
    RECOMMENDATION_MINIMUM_SAMPLES,
    RECOMMENDATION_WINDOW_SECONDS,
)
from custom_components.sat.types import HeatingCurveRecommendation


def _fill(advisor: HeatingCurveAdvisor, correction: float, *, count: int = RECOMMENDATION_MINIMUM_SAMPLES, target: float = 21.0, outside: float = 5.0) -> None:
    for index in range(count):
        advisor.record(
            timestamp=index * 60.0,
            correction=correction,
            target_temperature=target,
            outside_temperature=outside,
        )


def test_insufficient_samples():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, count=RECOMMENDATION_MINIMUM_SAMPLES - 1)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.INSUFFICIENT_SAMPLES
    assert advice.suggested_coefficient is None


def test_sustained_positive_correction_recommends_increase():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0)

    advice = advisor.advice
    shape = HeatingCurve.calculate(21.0, 5.0)

    assert advice.recommendation is HeatingCurveRecommendation.INCREASE
    assert advice.median_correction == 5.0
    assert advice.suggested_coefficient == round(1.8 + 4 * 5.0 / shape, 1)


def test_sustained_negative_correction_recommends_decrease():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, -2.0)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.DECREASE
    assert advice.suggested_coefficient < 1.8


def test_small_correction_holds():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 0.2)

    advice = advisor.advice

    assert advice.recommendation is HeatingCurveRecommendation.HOLD
    assert advice.suggested_coefficient == 1.8


def test_mild_weather_samples_are_skipped():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, target=20.0, outside=19.5)

    advice = advisor.advice

    assert advice.sample_count == 0
    assert advice.recommendation is HeatingCurveRecommendation.INSUFFICIENT_SAMPLES


def test_window_prunes_old_samples():
    advisor = HeatingCurveAdvisor(coefficient=1.8)
    _fill(advisor, 5.0, count=RECOMMENDATION_MINIMUM_SAMPLES)

    advisor.record(
        timestamp=RECOMMENDATION_WINDOW_SECONDS * 2,
        correction=5.0,
        target_temperature=21.0,
        outside_temperature=5.0,
    )

    assert advisor.advice.sample_count == 1
