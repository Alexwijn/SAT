"""Tests for SAT binary sensor entities."""

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er, issue_registry as ir

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
    await coordinator.async_set_boiler_temperature(40.0)

    for i in range(60):
        current_time = float(i * 60)
        pressure = 1.8 - (i * 0.01)
        await coordinator.async_set_boiler_pressure(pressure)
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

    state = hass.states.get(_get_pressure_entity_id(hass, entry))
    assert state is not None
    assert state.state == "on"
    assert state.attributes["pressure_drop_rate_bar_per_hour"] is not None


async def test_pressure_health_ignores_thermal_contraction(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)

    for i in range(60):
        current_time = float(i * 60)
        await coordinator.async_set_boiler_temperature(60.0 - i * 0.5)
        await coordinator.async_set_boiler_pressure(2.0 - (i * 0.01))
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

        state = hass.states.get(_get_pressure_entity_id(hass, entry))
        assert state.state == "off", f"False positive at iteration {i}"


async def test_pressure_health_ignores_quantized_chatter(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0

    def fake_timestamp():
        return current_time

    monkeypatch.setattr(sat_binary_sensor, "timestamp", fake_timestamp)
    await coordinator.async_set_boiler_temperature(40.0)
    readings = (2.0, 1.89, 1.8, 1.89, 1.8, 1.7, 1.8, 1.89)

    for i in range(240):
        current_time = float(i * 15)
        await coordinator.async_set_boiler_pressure(readings[i % len(readings)])
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

        state = hass.states.get(_get_pressure_entity_id(hass, entry))
        assert state.state == "off", f"False positive at iteration {i}"


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


def _get_fault_entity_id(hass, entry):
    registry = er.async_get(hass)
    return registry.async_get_entity_id("binary_sensor", "sat", f"{entry.entry_id}-boiler-fault")


async def test_device_fault_sensor(hass, coordinator, entry, domains, data, options, config):
    entity_id = _get_fault_entity_id(hass, entry)
    assert hass.states.get(entity_id).state == "unavailable"

    coordinator._fault_active = True
    coordinator._fault_code = 133
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["fault_code"] == 133


async def test_device_fault_creates_repair_issue(hass, coordinator, entry, domains, data, options, config):
    coordinator._fault_active = True
    coordinator._fault_code = 133
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"boiler_fault_{entry.entry_id}") is not None

    coordinator._fault_active = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, f"boiler_fault_{entry.entry_id}") is None


async def test_pressure_health_restore_after_long_downtime_is_not_stale(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 100.0

    monkeypatch.setattr(sat_binary_sensor, "timestamp", lambda: current_time)

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

    current_time = 100.0 + 4000.0
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(_get_pressure_entity_id(hass, entry)).state == "off"


async def test_pressure_health_keeps_problem_while_pressure_is_missing(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0
    monkeypatch.setattr(sat_binary_sensor, "timestamp", lambda: current_time)

    for current_time in (0.0, 130.0):
        await coordinator.async_set_boiler_pressure(0.6)
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

    assert hass.states.get(_get_pressure_entity_id(hass, entry)).state == "on"

    current_time = 200.0
    coordinator._boiler_pressure = None
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(_get_pressure_entity_id(hass, entry)).state == "on"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"boiler_pressure_{entry.entry_id}") is not None


async def test_pressure_health_clears_drop_rate_attribute_when_rate_is_unknown(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    current_time = 0.0
    monkeypatch.setattr(sat_binary_sensor, "timestamp", lambda: current_time)
    await coordinator.async_set_boiler_temperature(40.0)

    for i in range(60):
        current_time = float(i * 60)
        await coordinator.async_set_boiler_pressure(1.8 - (i * 0.01))
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

    assert hass.states.get(_get_pressure_entity_id(hass, entry)).attributes["pressure_drop_rate_bar_per_hour"] is not None

    await coordinator.async_set_boiler_temperature(20.0)
    current_time += 60.0
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(_get_pressure_entity_id(hass, entry)).attributes["pressure_drop_rate_bar_per_hour"] is None


def test_pressure_health_does_not_record_volatile_attributes(domains, data, options, config):
    assert sat_binary_sensor.SatPressureHealthSensor._unrecorded_attributes == {
        "pressure",
        "smoothed_pressure",
        "last_pressure",
        "last_pressure_timestamp",
        "last_seen_pressure_timestamp",
    }


async def test_pressure_health_restores_an_active_problem(hass, coordinator, entry, domains, data, options, config, monkeypatch):
    monkeypatch.setattr(sat_binary_sensor, "timestamp", lambda: 150.0)

    await hass.config_entries.async_unload(entry.entry_id)

    async def fake_async_get_last_state(self):
        return State(self.entity_id, "on", attributes={"last_seen_pressure_timestamp": 100.0})

    monkeypatch.setattr(sat_binary_sensor.SatPressureHealthSensor, "async_get_last_state", fake_async_get_last_state)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(_get_pressure_entity_id(hass, entry)).state == "on"


async def test_removing_the_entry_deletes_the_boiler_repairs(hass, coordinator, entry, domains, data, options, config, heating_control, monkeypatch):
    current_time = 0.0
    monkeypatch.setattr(sat_binary_sensor, "timestamp", lambda: current_time)

    for current_time in (0.0, 130.0):
        await coordinator.async_set_boiler_pressure(0.6)
        coordinator._fault_active = True
        coordinator._fault_code = 133
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

    registry = ir.async_get(hass)
    issue_ids = [f"boiler_pressure_{entry.entry_id}", f"boiler_fault_{entry.entry_id}"]
    assert all(registry.async_get_issue(DOMAIN, issue_id) is not None for issue_id in issue_ids)

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert all(registry.async_get_issue(DOMAIN, issue_id) is None for issue_id in issue_ids)
