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
