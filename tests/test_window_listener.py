"""Tests for window sensor listener registration."""

from datetime import timedelta

import pytest
from homeassistant.components.climate import PRESET_ACTIVITY, PRESET_COMFORT, PRESET_HOME, PRESET_NONE
from homeassistant.core import State
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import assert_setup_component, async_fire_time_changed, MockConfigEntry, mock_restore_cache

from custom_components.sat.const import CONF_HEATING_SYSTEM, CONF_WINDOW_SENSORS, DOMAIN
from custom_components.sat.types import HeatingSystem
from tests.const import DEFAULT_USER_DATA

pytestmark = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [
        (
            [],
            {
                CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
            },
            {
                CONF_WINDOW_SENSORS: ["binary_sensor.kitchen_window", "binary_sensor.bedroom_window"],
            },
            {},
        ),
    ],
)


@pytest.fixture
async def entry(hass, domains, data, options, config, caplog) -> MockConfigEntry:
    """Set up a SAT entry with a stable entry_id."""
    for domain, count in domains:
        with assert_setup_component(count, domain):
            assert await async_setup_component(hass, domain, config)

        await hass.async_block_till_done()

    await hass.async_start()
    await hass.async_block_till_done()

    user_data = DEFAULT_USER_DATA.copy()
    user_data.update(data)

    config_entry = MockConfigEntry(domain=DOMAIN, data=user_data, options=options, entry_id="entry-123")
    await hass.config_entries.async_add(config_entry)
    await hass.async_block_till_done()

    return config_entry


@pytest.fixture
async def climate(hass, entry):
    return hass.data[DOMAIN][entry.entry_id].climate


async def test_window_listener_uses_configured_entities(monkeypatch, hass, climate):
    expected_entity = "binary_sensor.kitchen_window"
    captured = []

    def _capture(_hass, entity_ids, _action):
        captured.append(entity_ids)
        return lambda: None

    monkeypatch.setattr("custom_components.sat.contact_sensors.async_track_state_change_event", _capture)

    climate._event_listeners_registered = False
    climate._register_event_listeners()

    assert any(expected_entity in (entity_ids or []) for entity_ids in captured)


KITCHEN = "binary_sensor.kitchen_window"
BEDROOM = "binary_sensor.bedroom_window"


async def _report(hass, entity_id, state):
    hass.states.async_set(entity_id, state)
    await hass.async_block_till_done()


async def _wait(hass, seconds=16):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_closing_one_of_two_windows_keeps_heating_paused(hass, climate):
    await climate.async_set_preset_mode(PRESET_COMFORT)
    await _report(hass, KITCHEN, "on")
    await _report(hass, BEDROOM, "on")
    await _wait(hass)

    assert climate.preset_mode == PRESET_ACTIVITY

    await _report(hass, KITCHEN, "off")

    assert climate.preset_mode == PRESET_ACTIVITY

    await _report(hass, BEDROOM, "off")

    assert climate.preset_mode == PRESET_COMFORT


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_sensor_lost_while_open_counts_as_closed(hass, climate, state):
    await climate.async_set_preset_mode(PRESET_HOME)
    await _report(hass, KITCHEN, "on")
    await _wait(hass)

    await _report(hass, KITCHEN, state)

    assert climate.preset_mode == PRESET_HOME


async def test_closing_before_the_minimum_open_time_changes_nothing(hass, climate):
    await climate.async_set_preset_mode(PRESET_HOME)
    await _report(hass, KITCHEN, "on")
    await _report(hass, KITCHEN, "off")
    await _wait(hass)

    assert climate.preset_mode == PRESET_HOME


async def test_custom_temperature_returns_after_closing(hass, climate):
    await climate.async_set_temperature(temperature=20.7)
    await _report(hass, KITCHEN, "on")
    await _wait(hass)
    await _report(hass, KITCHEN, "off")

    assert climate.preset_mode == PRESET_NONE
    assert climate.target_temperature == pytest.approx(20.7, abs=0.5)


async def test_chosen_activity_preset_survives_a_window(hass, climate):
    await climate.async_set_preset_mode(PRESET_ACTIVITY)
    await _report(hass, KITCHEN, "on")
    await _wait(hass)
    await _report(hass, KITCHEN, "off")

    assert climate.preset_mode == PRESET_ACTIVITY


async def test_preset_chosen_during_the_pause_is_kept(hass, climate):
    await climate.async_set_preset_mode(PRESET_HOME)
    await _report(hass, KITCHEN, "on")
    await _wait(hass)
    await climate.async_set_preset_mode(PRESET_COMFORT)
    await _report(hass, KITCHEN, "off")

    assert climate.preset_mode == PRESET_COMFORT


async def _set_up(hass, data, options):
    entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, **data}, options=options, entry_id="entry-restart")
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()

    return hass.data[DOMAIN][entry.entry_id].climate


async def test_window_open_at_startup_pauses_heating(hass, domains, data, options, config):
    hass.states.async_set(KITCHEN, "on")

    climate = await _set_up(hass, data, options)
    await _wait(hass)

    assert climate.preset_mode == PRESET_ACTIVITY


async def test_window_closed_during_a_restart_restores_the_preset(hass, domains, data, options, config):
    mock_restore_cache(hass, [State("climate.test", "heat", {"preset_mode": PRESET_ACTIVITY, "pre_activity_preset": PRESET_COMFORT, "temperature": 10.0})])
    hass.states.async_set(KITCHEN, "off")

    climate = await _set_up(hass, data, options)

    assert climate.preset_mode == PRESET_COMFORT


async def test_close_delay_waits_before_restoring(hass, climate):
    climate._contact_sensors._close_delay = 120
    await climate.async_set_preset_mode(PRESET_HOME)
    await _report(hass, KITCHEN, "on")
    await _wait(hass)
    await _report(hass, KITCHEN, "off")

    assert climate.preset_mode == PRESET_ACTIVITY

    await _wait(hass, 200)

    assert climate.preset_mode == PRESET_HOME


async def test_pause_for_a_window_is_the_reason_heating_is_blocked(hass, climate):
    await climate.async_set_hvac_mode("heat")
    await climate.async_set_preset_mode(PRESET_HOME)
    await _report(hass, KITCHEN, "on")
    await _wait(hass)
    climate._demand_control._fail_safe.update(None, 1000.0)

    assert climate.extra_state_attributes["heating_blocked_by"] == "window"

    await climate.async_set_preset_mode(PRESET_COMFORT)

    assert climate.extra_state_attributes["heating_blocked_by"] != "window"


async def test_removing_the_sensors_ends_a_running_pause(hass, domains, data, options, config):
    mock_restore_cache(hass, [State("climate.test", "heat", {"preset_mode": PRESET_ACTIVITY, "pre_activity_preset": PRESET_COMFORT, "temperature": 10.0})])

    climate = await _set_up(hass, data, {})

    assert climate.preset_mode == PRESET_COMFORT
