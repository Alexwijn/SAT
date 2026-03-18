"""Tests for SAT binary sensor entities."""

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er

from custom_components.sat.const import CONF_HEATING_SYSTEM, DOMAIN
from custom_components.sat.types import HeaterState, HeatingSystem
import custom_components.sat.binary_sensor as sat_binary_sensor

pytestmark = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [
        (
            [],
            {CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS},
            {},
            {},
        ),
    ],
)


def _get_pressure_entity_id(hass, entry):
    registry = er.async_get(hass)
    return registry.async_get_entity_id("binary_sensor", "sat", f"{entry.entry_id}-pressure-health")


async def test_pressure_health_low_pressure(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)

    await coordinator.async_set_boiler_pressure(0.6)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state is not None
    assert state.state == "off"

    current_time = 130.0
    await coordinator.async_set_boiler_pressure(0.6)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state.state == "on"


async def test_pressure_health_normal_pressure(hass, coordinator, entry, domains, data, options, config):
    await coordinator.async_set_boiler_pressure(1.5)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state is not None
    assert state.state == "off"


async def test_pressure_health_drop_rate(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)

    for i in range(60):
        current_time = float(i * 10)
        pressure = 1.8 - (i * 0.01)
        await coordinator.async_set_boiler_pressure(pressure)
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state is not None
    assert state.state == "on"
    assert state.attributes["pressure_drop_rate_bar_per_hour"] is not None


async def test_pressure_health_ignores_drop_rate_after_shutdown(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_pressure(1.8)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    current_time = 10.0
    await coordinator.async_set_heater_state(HeaterState.OFF)
    await coordinator.async_set_boiler_pressure(1.7)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state is not None
    assert state.state == "off"
    assert state.attributes["pressure_drop_rate_bar_per_hour"] is None


async def test_pressure_health_stale_pressure(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)

    await coordinator.async_set_boiler_pressure(1.5)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    current_time = 4000.0
    coordinator._boiler_pressure = None
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state is not None
    assert state.state == "on"


async def test_pressure_health_restores_last_pressure(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 150.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)

    if entry.entry_id in hass.data.get(DOMAIN, {}):
        await hass.config_entries.async_unload(entry.entry_id)

    restored_attrs = {
        "last_pressure": 1.4,
        "last_pressure_timestamp": 100.0,
        "last_seen_pressure_timestamp": 100.0,
        "smoothed_pressure": 1.4,
    }

    async def fake_async_get_last_state(self):
        return State(self.entity_id, "off", attributes=restored_attrs)

    monkeypatch.setattr(sat_binary_sensor.SatPressureHealthSensor, "async_get_last_state", fake_async_get_last_state)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state is not None
    assert state.attributes["last_pressure"] == 1.4
    assert state.attributes["smoothed_pressure"] is not None


async def test_pressure_health_normal_oscillations_no_false_positive(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)

    for i in range(60):
        current_time = float(i * 30)
        pressure = 2.1 + (0.2 if i % 2 == 0 else -0.2)
        await coordinator.async_set_boiler_pressure(pressure)
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

        state = hass.states.get(_get_pressure_entity_id(hass, entry))
        assert state.state == "off", f"False positive at iteration {i}, pressure={pressure}"


async def test_pressure_health_confirmation_delay_resets(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)

    await coordinator.async_set_boiler_pressure(0.6)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state.state == "off"

    current_time = 100.0
    await coordinator.async_set_boiler_pressure(0.6)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state.state == "off"

    current_time = 110.0
    await coordinator.async_set_boiler_pressure(1.5)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state.state == "off"

    current_time = 120.0
    await coordinator.async_set_boiler_pressure(0.6)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    current_time = 250.0
    await coordinator.async_set_boiler_pressure(0.6)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state.state == "on"


async def test_pressure_health_high_pressure_with_delay(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)

    await coordinator.async_set_boiler_pressure(3.0)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state.state == "off"

    current_time = 130.0
    await coordinator.async_set_boiler_pressure(3.0)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state.state == "on"
