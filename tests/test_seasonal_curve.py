"""Tests for the seasonal heating curve fit."""

from custom_components.sat.heating_curve import HeatingCurveAdviceSample, HeatingCurveAdvisor, RECOMMENDATION_MINIMUM_SHAPE


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
