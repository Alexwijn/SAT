"""Test setup process."""

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat import async_reload_entry
from custom_components.sat.const import DOMAIN
from tests.const import DEFAULT_USER_DATA


async def test_setup_update_unload_entry(hass):
    """Test entry setup and unload."""
    # Create the switch entry
    switch_entry = MockConfigEntry(domain=SWITCH_DOMAIN, entry_id="switch.test")
    await hass.config_entries.async_add(switch_entry)

    # Create our entity
    sat_entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    await hass.config_entries.async_add(sat_entry)

    # Wait till there are no tasks and see if we have been configured
    await hass.async_block_till_done()
    assert DOMAIN in hass.data and sat_entry.entry_id in hass.data[DOMAIN]

    # Reload the entry without errors
    assert await async_reload_entry(hass, sat_entry) is None
