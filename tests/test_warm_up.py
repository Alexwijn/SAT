"""Unit tests for the warm-up rate estimator."""

from custom_components.sat.warm_up import WarmUpEstimator, WARM_UP_MINIMUM_SPAN_SECONDS


def test_learns_rate_from_active_heating():
    estimator = WarmUpEstimator()

    for index in range(31):
        estimator.update(active=True, temperature=18.0 + index * 0.025, timestamp=index * 60.0)

    assert estimator.rate is not None
    assert estimator.rate == 1.5


def test_inactive_periods_reset_the_window():
    estimator = WarmUpEstimator()

    for index in range(20):
        estimator.update(active=True, temperature=18.0 + index * 0.025, timestamp=index * 60.0)

    estimator.update(active=False, temperature=18.5, timestamp=20 * 60.0)

    for index in range(20):
        estimator.update(active=True, temperature=18.5 + index * 0.025, timestamp=(21 + index) * 60.0)

    assert estimator.rate is None


def test_smooths_rate_with_previous_value():
    estimator = WarmUpEstimator()
    estimator.restore(1.0)

    timestamp = 0.0
    temperature = 18.0
    while timestamp <= WARM_UP_MINIMUM_SPAN_SECONDS:
        estimator.update(active=True, temperature=temperature, timestamp=timestamp)
        timestamp += 60.0
        temperature += 0.025

    assert estimator.rate == round(0.3 * 1.5 + 0.7 * 1.0, 2)


def test_restore_handles_unknown():
    estimator = WarmUpEstimator()
    estimator.restore(None)

    assert estimator.rate is None
