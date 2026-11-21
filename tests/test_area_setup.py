"""Tests for room PID availability right after the climate is set up on a running instance."""

import pytest
from homeassistant.components.climate import HVACMode

from custom_components.sat.const import CONF_HEATING_SYSTEM, CONF_ROOMS
from custom_components.sat.types import HeatingSystem

pytestmark = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [
        (
            [],
            {
                CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
                CONF_ROOMS: ["climate.room1"],
            },
            {},
            {},
        ),
    ],
)


@pytest.fixture
async def room_states(hass):
    hass.states.async_set("climate.room1", HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 19.0})
    hass.states.async_set("sensor.test_inside_sensor", "20.0")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")


async def test_room_pids_available_right_after_setup(room_states, climate, demand_control):
    area = demand_control.areas.get("climate.room1")

    assert area is not None
    assert area.heating_curve.value is not None
    assert area.pid.available is True
    assert area.pid.output > 0
