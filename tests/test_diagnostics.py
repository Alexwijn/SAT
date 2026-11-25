"""Tests for the diagnostics platform."""

from homeassistant.components.diagnostics import REDACTED
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.const import CONF_DEVICE, DOMAIN
from custom_components.sat.diagnostics import async_get_config_entry_diagnostics
from tests.const import DEFAULT_USER_DATA


async def test_config_entry_diagnostics(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, CONF_DEVICE: "very-secret-device"})
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["data"][CONF_DEVICE] == REDACTED
    assert diagnostics["coordinator"]["type"] is not None
    assert "pwm" in diagnostics["heating_control"]
    assert "climate" in diagnostics
    assert "curve_advisor" in diagnostics["climate"]
    assert diagnostics["climate"]["heating_blocked_by"] == "hvac_off"

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
