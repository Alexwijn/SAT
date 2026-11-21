"""Tests for the water heater platform."""

import pytest
from homeassistant.components.water_heater import (
    ATTR_OPERATION_MODE,
    DOMAIN as WATER_HEATER_DOMAIN,
    SERVICE_SET_OPERATION_MODE,
    STATE_GAS,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, UnitOfTemperature
from homeassistant.helpers import entity_registry as er

from custom_components.sat.const import CONF_HEATING_SYSTEM, DOMAIN, HeatingSystem
from custom_components.sat.water_heater import SatWaterHeater

pytestmark = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [
        (
            [],
            {
                CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
            },
            {},
            {},
        ),
    ],
)


@pytest.fixture
def entity_id(hass, entry) -> str:
    return er.async_get(hass).async_get_entity_id(WATER_HEATER_DOMAIN, DOMAIN, f"{entry.entry_id}-water-heater")


async def test_water_heater_entity_created(hass, entity_id, coordinator):
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == STATE_GAS


async def test_water_heater_turn_off_and_on(hass, entity_id, coordinator):
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPERATION_MODE: STATE_OFF},
        blocking=True,
    )

    assert coordinator.hot_water_enabled is False
    assert hass.states.get(entity_id).state == STATE_OFF

    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPERATION_MODE: STATE_GAS},
        blocking=True,
    )

    assert coordinator.hot_water_enabled is True
    assert hass.states.get(entity_id).state == STATE_GAS


async def test_water_heater_reports_its_temperatures_in_celsius(hass, entity_id, coordinator, monkeypatch):
    water_heater = hass.data["water_heater"].get_entity(entity_id)
    monkeypatch.setattr(type(coordinator), "hot_water_setpoint", property(lambda self: 55.0))

    assert water_heater.native_temperature_unit == UnitOfTemperature.CELSIUS
    assert water_heater.native_target_temperature == water_heater.target_temperature == 55.0


async def test_water_heater_provides_the_native_temperature_api_beside_the_legacy_one(entry):
    for name in ("temperature_unit", "target_temperature"):
        assert {name, f"native_{name}"} <= set(vars(SatWaterHeater))
