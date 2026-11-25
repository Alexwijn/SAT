from concurrent.futures import ThreadPoolExecutor

import pytest

from custom_components.sat.helpers import timestamp
from custom_components.sat.temperature.history import DAILY_WINDOW_SECONDS, TemperatureHistory


def test_temperature_history_statistics():
    history = TemperatureHistory()
    base = timestamp()

    history.record(-1.0, base)
    history.record(0.0, base + 1800.0)
    history.record(1.0, base + 3600.0)

    statistics = history.statistics
    recent = statistics.window.recent
    daily = statistics.window.daily

    assert recent.sample_count == 3
    assert daily.sample_count == 3
    assert recent.median_error == pytest.approx(0.0, abs=0.01)
    assert recent.mean_error == pytest.approx(0.0, abs=0.01)
    assert recent.mean_abs_error == pytest.approx(2 / 3, abs=0.01)
    assert recent.in_band_fraction == pytest.approx(1 / 3, abs=0.01)


def test_temperature_history_daily_prune():
    history = TemperatureHistory()
    base = timestamp()

    history.record(1.0, base - DAILY_WINDOW_SECONDS - 20.0)
    history.record(1.0, base - 10.0)

    assert history.window_statistics.daily.sample_count == 1


def test_temperature_history_steadiness():
    history = TemperatureHistory()
    base = timestamp()

    for index in range(7):
        history.record(0.3 + (0.1 if index % 2 else -0.1), base + index * 300.0)

    assert history.is_steady(1800, 0.4) is True
    assert history.is_steady(1800, 0.1) is False

    drifting = TemperatureHistory()
    for index in range(7):
        drifting.record(1.0 - index * 0.1, base + index * 300.0)

    assert drifting.is_steady(1800, 0.4) is False

    short = TemperatureHistory()
    short.record(0.0, base)
    short.record(0.0, base + 60.0)
    short.record(0.0, base + 120.0)

    assert short.is_steady(1800, 0.4) is False


def test_temperature_history_median_error():
    history = TemperatureHistory()
    base = timestamp()

    assert history.median_error(1800) is None

    for index, error in enumerate([0.5, -0.3, -0.1, -0.2, -0.1]):
        history.record(error, base + index * 300.0)

    assert history.median_error(1800) == pytest.approx(-0.1)
    assert history.median_error(600) == pytest.approx(-0.1)


def test_temperature_history_read_during_record_and_prune():
    history = TemperatureHistory()

    with ThreadPoolExecutor(max_workers=1) as executor:
        class Timestamp(float):
            def __ge__(self, cutoff):
                # Force a write from another thread while the reader is filtering samples.
                executor.submit(history.record, 1.0, DAILY_WINDOW_SECONDS + 61.0).result(timeout=5)
                return super().__ge__(cutoff)

        history.record(0.0, Timestamp(0.0))
        history.record(0.0, 30.0)
        history.record(0.0, 60.0)

        assert history.is_steady(60, 0.1) is True

    assert history.is_steady(60, 0.1) is False
    assert history.window_statistics.daily.sample_count == 1
