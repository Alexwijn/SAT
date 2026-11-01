"""Tests for the long-term boiler health monitor."""

from datetime import datetime
from types import SimpleNamespace

from homeassistant.util import dt as dt_util

from custom_components.sat.boiler_health import BoilerHealthMonitor, HISTORY_RETENTION_DAYS, PRESSURE_SAMPLE_INTERVAL_SECONDS
from custom_components.sat.device import DeviceState
from custom_components.sat.types import CycleKind, Percentiles

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
