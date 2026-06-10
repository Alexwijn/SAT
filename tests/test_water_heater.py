"""Tests for the water heater platform."""

import pytest
from homeassistant.components.water_heater import (
    ATTR_OPERATION_MODE,
    DOMAIN as WATER_HEATER_DOMAIN,
    SERVICE_SET_OPERATION_MODE,
    STATE_GAS,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF

from custom_components.sat.const import CONF_HEATING_SYSTEM, HeatingSystem

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

ENTITY_ID = "water_heater.hot_water"


async def test_water_heater_entity_created(hass, entry, coordinator):
    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert state.state == STATE_GAS


async def test_water_heater_turn_off_and_on(hass, entry, coordinator):
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_OPERATION_MODE: STATE_OFF},
        blocking=True,
    )

    assert coordinator.hot_water_enabled is False
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_OPERATION_MODE: STATE_GAS},
        blocking=True,
    )

    assert coordinator.hot_water_enabled is True
    assert hass.states.get(ENTITY_ID).state == STATE_GAS
