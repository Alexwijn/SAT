"""Test setup process."""

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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


async def test_unique_id_migration(hass):
    """Ensure name-based unique IDs are migrated to entry_id-based IDs."""
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA, entry_id="entry-migrate")
    entry.add_to_hass(hass)

    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)

    device = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "Test")},
        name="SAT",
    )
    entity_entry = entity_reg.async_get_or_create(
        "climate",
        DOMAIN,
        "test",
        config_entry=entry,
        device_id=device.id,
    )

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    updated_entity = entity_reg.async_get(entity_entry.entity_id)
    assert updated_entity is not None
    assert updated_entity.unique_id == entry.entry_id
    assert updated_entity.entity_id == entity_entry.entity_id

    migrated_device = device_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert migrated_device is not None
    assert (DOMAIN, "Test") not in migrated_device.identifiers
