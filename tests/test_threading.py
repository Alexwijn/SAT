import threading
from datetime import timedelta
from unittest.mock import Mock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.sat.boiler_health import BoilerHealthMonitor
from custom_components.sat.const import CONF_HEATING_SYSTEM, CONF_ROOMS, EVENT_SAT_CYCLE_ENDED
from custom_components.sat.pid import PID_UPDATE_INTERVAL
from custom_components.sat.temperature.history import TemperatureHistory
from custom_components.sat.types import HeatingSystem
from custom_components.sat.wind import WindMonitor

ROOM = "climate.room1"

pytestmark = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [([], {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS, CONF_ROOMS: [ROOM]}, {}, {})],
)


@pytest.fixture
async def room_states(hass):
    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 19.0})
    hass.states.async_set("sensor.test_inside_sensor", "20.0")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")


@pytest.fixture
def record_threads(monkeypatch):
    def _record(owner, method):
        threads = set()
        monkeypatch.setattr(owner, method, lambda *args, **kwargs: threads.add(threading.get_ident()))

        return threads

    return _record


async def test_wind_is_observed_in_the_event_loop(room_states, hass, climate, record_threads):
    threads = record_threads(WindMonitor, "observe")

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=PID_UPDATE_INTERVAL + 1))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert threads == {threading.get_ident()}


async def test_room_history_is_recorded_in_the_event_loop(room_states, hass, climate, record_threads):
    threads = record_threads(TemperatureHistory, "record")

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=PID_UPDATE_INTERVAL + 1))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert threads == {threading.get_ident()}


async def test_ended_cycle_is_recorded_in_the_event_loop(hass, climate, heating_control, record_threads, monkeypatch):
    threads = record_threads(BoilerHealthMonitor, "record_cycle")
    monkeypatch.setattr(heating_control._pwm, "on_cycle_end", Mock())

    hass.bus.async_fire(EVENT_SAT_CYCLE_ENDED, {"cycle": Mock()})
    await hass.async_block_till_done()

    assert threads == {threading.get_ident()}
