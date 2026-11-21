"""Tests for the names of the SAT device and its entities."""

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.sat.const import CONF_HEATING_SYSTEM, CONF_NAME, DOMAIN, HeatingSystem

pytestmark = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [([], {CONF_NAME: "Living Room", CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS}, {}, {})],
)


def _entity_id(hass, domain: str, unique_id: str) -> str:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


async def test_device_is_named_after_the_thermostat(hass, entry):
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)

    assert [device.name for device in devices] == ["Living Room"]


async def test_climate_is_named_after_the_thermostat_once(hass, entry):
    assert hass.states.get(_entity_id(hass, "climate", entry.entry_id)).name == "Living Room"


async def test_entities_are_not_named_after_the_thermostat_twice(hass, entry):
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    names = [hass.states.get(entity.entity_id).name for entity in entities if hass.states.get(entity.entity_id) is not None]

    assert names
    assert [name for name in names if name.count("Living Room") > 1] == []
