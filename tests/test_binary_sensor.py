"""Tests for SAT binary sensor entities."""

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er

from custom_components.sat.const import CONF_HEATING_SYSTEM, DOMAIN
from custom_components.sat.types import HeatingSystem
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


async def test_pressure_health_low_pressure(hass, coordinator, entry, domains, data, options, config):
    await coordinator.async_set_boiler_pressure(0.6)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("binary_sensor", "sat", f"{entry.entry_id}-pressure-health")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"


async def test_pressure_health_normal_pressure(hass, coordinator, entry, domains, data, options, config):
    await coordinator.async_set_boiler_pressure(1.5)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("binary_sensor", "sat", f"{entry.entry_id}-pressure-health")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "off"


async def test_pressure_health_drop_rate(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)

    await coordinator.async_set_boiler_pressure(1.8)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    current_time = 1800.0
    await coordinator.async_set_boiler_pressure(1.6)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    current_time = 3600.0
    await coordinator.async_set_boiler_pressure(1.2)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("binary_sensor", "sat", f"{entry.entry_id}-pressure-health")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"
    assert state.attributes["pressure_drop_rate_bar_per_hour"] is not None


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

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("binary_sensor", "sat", f"{entry.entry_id}-pressure-health")
    state = hass.states.get(entity_id)
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
    }

    async def fake_async_get_last_state(self):
        return State(self.entity_id, "off", attributes=restored_attrs)

    monkeypatch.setattr(sat_binary_sensor.SatPressureHealthSensor, "async_get_last_state", fake_async_get_last_state)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("binary_sensor", "sat", f"{entry.entry_id}-pressure-health")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["last_pressure"] == 1.4
