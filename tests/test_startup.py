import threading
from unittest.mock import AsyncMock

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.climate import SatClimate
from custom_components.sat.const import DOMAIN
from custom_components.sat.demand_control import SatDemandControl
from tests.const import DEFAULT_USER_DATA


async def test_startup_handlers_run_in_the_event_loop(hass, monkeypatch):
    loop_thread = threading.get_ident()
    threads = {}

    def _record(name):
        return lambda *args, **kwargs: threads.setdefault(name, threading.get_ident())

    monkeypatch.setattr("homeassistant.components.dhcp.async_setup", AsyncMock(return_value=True))
    hass.set_state(CoreState.not_running)
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()

    monkeypatch.setattr(SatClimate, "_register_event_listeners", _record("climate"))
    monkeypatch.setattr(SatDemandControl, "_register_event_listeners", _record("demand"))
    monkeypatch.setattr(SatDemandControl, "schedule_heating_control_loop", _record("schedule"))

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert threads == {"climate": loop_thread, "demand": loop_thread, "schedule": loop_thread}
