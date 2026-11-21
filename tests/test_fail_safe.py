from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util

from custom_components.sat.const import CONF_INSIDE_SENSOR_ENTITY_ID, CONF_ROOMS, FAIL_SAFE_GRACE_SECONDS
from custom_components.sat.fail_safe import FailSafeMonitor
from custom_components.sat.types import ControlSignal, FailSafePhase


def test_monitor_holds_then_releases():
    monitor = FailSafeMonitor()

    assert monitor.update(ControlSignal.INSIDE_TEMPERATURE, 1000.0).phase is FailSafePhase.HOLDING
    assert monitor.update(ControlSignal.INSIDE_TEMPERATURE, 999.0 + FAIL_SAFE_GRACE_SECONDS).phase is FailSafePhase.HOLDING

    released = monitor.update(ControlSignal.OUTSIDE_TEMPERATURE, 1000.0 + FAIL_SAFE_GRACE_SECONDS)

    assert released.phase is FailSafePhase.RELEASED
    assert released.just_released is True
    assert monitor.update(ControlSignal.OUTSIDE_TEMPERATURE, 1010.0 + FAIL_SAFE_GRACE_SECONDS).just_released is False


def test_monitor_restarts_the_grace_period_after_the_signal_returns():
    monitor = FailSafeMonitor()
    monitor.update(ControlSignal.INSIDE_TEMPERATURE, 1000.0)
    monitor.update(ControlSignal.INSIDE_TEMPERATURE, 1000.0 + FAIL_SAFE_GRACE_SECONDS)

    assert monitor.update(None, 2000.0).just_resumed is True
    assert monitor.update(ControlSignal.INSIDE_TEMPERATURE, 2010.0).phase is FailSafePhase.HOLDING


INSIDE = "sensor.test_inside_sensor"
OUTSIDE = "sensor.test_outside_sensor"
ROOM = "climate.bedroom"

MAIN_ROOM = pytest.mark.parametrize(("domains", "data", "options", "config"), [([], {}, {}, {})])
ROOMS_ONLY = pytest.mark.parametrize(("domains", "data", "options", "config"), [([], {CONF_INSIDE_SENSOR_ENTITY_ID: None, CONF_ROOMS: [ROOM]}, {}, {})])


@pytest.fixture
def commands(hass, demand_control, heating_control, monkeypatch):
    hass.states.async_set(INSIDE, "20")
    hass.states.async_set(OUTSIDE, "5")
    demand_control._hvac_mode = HVACMode.HEAT
    demand_control._target_temperature = 21.0

    update, release = AsyncMock(), AsyncMock()
    monkeypatch.setattr(heating_control, "update", update)
    monkeypatch.setattr(heating_control, "async_release", release)

    return update, release


async def _run(demand_control, offset: float = 0.0) -> None:
    await demand_control.async_control_heating_loop(dt_util.utc_from_timestamp(1_800_000_000.0 + offset))


@MAIN_ROOM
@pytest.mark.parametrize("sensor", [INSIDE, OUTSIDE])
async def test_last_demand_is_held_while_a_signal_is_missing(hass, demand_control, commands, sensor, domains, data, options, config):
    update, release = commands
    await _run(demand_control)
    held = update.call_args.args[0]

    hass.states.async_set(sensor, "unavailable")
    await _run(demand_control, 60.0)

    replayed = update.call_args.args[0]
    assert replayed.requested_setpoint == held.requested_setpoint
    assert replayed.timestamp == held.timestamp + 60.0
    release.assert_not_awaited()


@MAIN_ROOM
async def test_nothing_is_sent_while_waiting_without_an_earlier_demand(hass, demand_control, commands, domains, data, options, config):
    update, release = commands
    hass.states.async_set(INSIDE, "unavailable")

    await _run(demand_control)

    update.assert_not_awaited()
    release.assert_not_awaited()


@MAIN_ROOM
async def test_boiler_is_released_and_the_pid_reset_once_after_the_grace_period(hass, demand_control, commands, monkeypatch, domains, data, options, config):
    update, release = commands
    resets = []
    monkeypatch.setattr(demand_control, "reset_control_state", lambda: resets.append(True))
    hass.states.async_set(OUTSIDE, "unavailable")

    await _run(demand_control)
    await _run(demand_control, FAIL_SAFE_GRACE_SECONDS)
    await _run(demand_control, FAIL_SAFE_GRACE_SECONDS + 5.0)

    update.assert_not_awaited()
    assert release.await_count == 2
    assert resets == [True]


@MAIN_ROOM
async def test_control_resumes_when_the_signal_returns(hass, demand_control, commands, domains, data, options, config):
    update, release = commands
    hass.states.async_set(OUTSIDE, "unavailable")
    await _run(demand_control)
    await _run(demand_control, FAIL_SAFE_GRACE_SECONDS)

    hass.states.async_set(OUTSIDE, "5")
    await _run(demand_control, FAIL_SAFE_GRACE_SECONDS + 5.0)

    update.assert_awaited_once()
    assert demand_control.heating_curve.value is not None


@MAIN_ROOM
async def test_hvac_off_is_neither_held_nor_released(hass, demand_control, commands, domains, data, options, config):
    update, release = commands
    demand_control._hvac_mode = HVACMode.OFF
    hass.states.async_set(INSIDE, "unavailable")

    await _run(demand_control)
    await _run(demand_control, FAIL_SAFE_GRACE_SECONDS)

    assert update.call_args.args[0].hvac_mode is HVACMode.OFF
    release.assert_not_awaited()


@ROOMS_ONLY
@pytest.mark.parametrize(("room_state", "released"), [("unavailable", True), ("off", False), ("heat", False)])
async def test_rooms_only_releases_when_no_room_can_be_reached(hass, demand_control, commands, room_state, released, domains, data, options, config):
    update, release = commands
    hass.states.async_set(ROOM, room_state, {"temperature": 21.0, "current_temperature": 20.0})

    await _run(demand_control)
    await _run(demand_control, FAIL_SAFE_GRACE_SECONDS)

    assert release.await_count == (1 if released else 0)
