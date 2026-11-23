"""Test setup process."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from sentry_sdk import get_client, get_global_scope

from custom_components.sat import async_reload_entry, async_remove_entry, async_unload_entry, initialize_sentry
from custom_components.sat.const import CONF_DEVICE, CONF_ROOMS, CONF_TARGET_TEMPERATURE_STEP, DOMAIN
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


async def test_unload_handles_already_unloaded_platforms(hass, monkeypatch):
    """Ensure unload handles already-unloaded platforms and still cleans up."""
    sat_entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    sat_entry.add_to_hass(hass)

    sentry = Mock()
    hass.data.setdefault(DOMAIN, {})[sat_entry.entry_id] = SimpleNamespace(sentry=sentry)

    async def _raise_value_error(*_args, **_kwargs):
        raise ValueError("Platforms already unloaded")

    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", _raise_value_error)

    assert await async_unload_entry(hass, sat_entry)
    sentry.flush.assert_called_once()
    sentry.close.assert_called_once()
    assert DOMAIN not in hass.data


async def test_reload_skips_setup_when_unload_fails(hass, monkeypatch):
    """Ensure reload does not proceed when unload fails."""
    sat_entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    await hass.config_entries.async_add(sat_entry)
    await hass.async_block_till_done()

    setup_mock = AsyncMock()
    unload_platforms_mock = AsyncMock(return_value=False)

    from custom_components import sat
    with monkeypatch.context() as patches:
        patches.setattr(sat, "async_setup_entry", setup_mock)
        patches.setattr(hass.config_entries, "async_unload_platforms", unload_platforms_mock)

        await async_reload_entry(hass, sat_entry)

    setup_mock.assert_not_called()
    unload_platforms_mock.assert_awaited_once()


async def test_options_updates_reload_entry_once(hass, caplog):
    """Ensure repeated options updates reload the entry without setting it up twice."""
    sat_entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    await hass.config_entries.async_add(sat_entry)
    await hass.async_block_till_done()

    for target_temperature_step in (0.1, 1.0):
        hass.config_entries.async_update_entry(sat_entry, options={CONF_TARGET_TEMPERATURE_STEP: target_temperature_step})
        await hass.async_block_till_done()

    assert "already been setup" not in caplog.text
    assert len(sat_entry.update_listeners) == 1
    assert sat_entry.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN][sat_entry.entry_id].config.options[CONF_TARGET_TEMPERATURE_STEP] == 1.0


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

    devices = dr.async_entries_for_config_entry(device_reg, entry.entry_id)
    assert [device.identifiers for device in devices] == [{(DOMAIN, entry.entry_id)}]


async def test_unload_releases_the_boiler_once(hass, monkeypatch):
    sat_entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    await hass.config_entries.async_add(sat_entry)
    await hass.async_block_till_done()

    release = AsyncMock()
    monkeypatch.setattr(hass.data[DOMAIN][sat_entry.entry_id].coordinator, "_async_release", release)

    assert await hass.config_entries.async_unload(sat_entry.entry_id)

    release.assert_awaited_once()


async def test_stopping_releases_before_other_integrations_stop(hass, monkeypatch):
    sat_entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    await hass.config_entries.async_add(sat_entry)
    await hass.async_block_till_done()

    entry_data = hass.data[DOMAIN][sat_entry.entry_id]
    released_at_stop = []
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _: released_at_stop.append(entry_data.coordinator.released))

    await hass.async_stop()

    assert released_at_stop == [True]


async def test_a_scheduled_run_does_not_command_a_released_boiler(hass, monkeypatch):
    sat_entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    await hass.config_entries.async_add(sat_entry)
    await hass.async_block_till_done()

    entry_data = hass.data[DOMAIN][sat_entry.entry_id]
    await entry_data.demand_control.async_stop_controlling()

    update = AsyncMock()
    monkeypatch.setattr(entry_data.heating_control, "update", update)
    await entry_data.demand_control.async_control_heating_loop()

    update.assert_not_awaited()


async def test_removing_the_entry_removes_its_stores(hass, hass_storage):
    sat_entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, CONF_DEVICE: "Fake", CONF_ROOMS: ["climate.bedroom"]})
    await hass.config_entries.async_add(sat_entry)
    await hass.async_block_till_done()

    climate = er.async_get(hass).async_get_entity_id("climate", DOMAIN, sat_entry.entry_id)
    keys = [f"sat.{name}.{sat_entry.entry_id}" for name in ("preheat", "room_balance", "wind_sensitivity", "boiler_health", "curve_advisor")]
    keys += [f"sat.pid.{climate}.Fake", "sat.pid.climate.bedroom.Fake", "sat.device.Fake", "sat.mqtt.Fake"]

    for key in keys + ["sat.preheat.another_entry"]:
        hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {}}

    await hass.config_entries.async_remove(sat_entry.entry_id)
    await hass.async_block_till_done()

    assert [key for key in keys if key in hass_storage] == []
    assert "sat.preheat.another_entry" in hass_storage


async def test_stores_of_a_device_stay_while_another_entry_uses_it(hass, hass_storage):
    first = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, CONF_DEVICE: "Fake"})
    second = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, CONF_DEVICE: "Fake"})
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    hass_storage["sat.device.Fake"] = {"version": 1, "minor_version": 1, "key": "sat.device.Fake", "data": {}}

    await async_remove_entry(hass, first)

    assert "sat.device.Fake" in hass_storage


async def test_renaming_the_climate_keeps_controlling(hass, monkeypatch):
    sat_entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    await hass.config_entries.async_add(sat_entry)
    await hass.async_block_till_done()

    entry_data = hass.data[DOMAIN][sat_entry.entry_id]
    climate = er.async_get(hass).async_get_entity_id("climate", DOMAIN, sat_entry.entry_id)

    er.async_get(hass).async_update_entity(climate, new_entity_id="climate.renamed")
    await hass.async_block_till_done()

    update = AsyncMock()
    monkeypatch.setattr(entry_data.heating_control, "update", update)
    entry_data.demand_control._hvac_mode = "off"
    await entry_data.demand_control.async_control_heating_loop()

    update.assert_awaited()


async def test_stopping_removes_the_listeners_without_errors(hass, caplog):
    sat_entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    await hass.config_entries.async_add(sat_entry)
    await hass.async_block_till_done()

    await hass.async_stop()

    assert "Unable to remove unknown job listener" not in caplog.text


def test_sentry_client_is_bound_to_the_global_scope(monkeypatch):
    client = Mock()
    monkeypatch.setattr("custom_components.sat.Client", Mock(return_value=client))

    try:
        assert initialize_sentry() is client
        assert get_client() is client
    finally:
        get_global_scope().set_client(None)
