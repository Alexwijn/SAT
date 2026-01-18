"""Tests for window sensor listener registration."""

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import assert_setup_component, MockConfigEntry

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
                CONF_WINDOW_SENSORS: ["binary_sensor.kitchen_window"],
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

    monkeypatch.setattr("custom_components.sat.climate.async_track_state_change_event", _capture)

    climate._register_event_listeners()

    assert any(expected_entity in (entity_ids or []) for entity_ids in captured)
