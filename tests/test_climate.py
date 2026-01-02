"""Tests focused on SAT climate setpoint and heating curve behavior."""

import pytest
from homeassistant.components.climate import HVACMode

from custom_components.sat.climate import (
    INCREASE_STEP_THRESHOLD_CELSIUS,
    NEAR_TARGET_MARGIN_CELSIUS,
    SatClimate,
)
from custom_components.sat.const import (
    CONF_HEATING_CURVE_COEFFICIENT,
    CONF_HEATING_SYSTEM,
    HEATING_MODE_COMFORT,
    HEATING_MODE_ECO,
    HEATING_SYSTEM_RADIATORS,
    MINIMUM_SETPOINT,
    OPTIONS_DEFAULTS,
)
from custom_components.sat.heating_curve import HeatingCurve
from custom_components.sat.pid import PID
from custom_components.sat.types import DeviceState

pytestmark = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [
        (
            [],
            {
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
            },
            {},
            {},
        ),
    ],
)


def test_requested_setpoint_without_heating_curve(climate):
    assert climate.heating_curve.value is None
    assert climate.requested_setpoint == MINIMUM_SETPOINT


def test_requested_setpoint_eco_mode_ignores_secondary_and_caps(monkeypatch, climate):
    climate._heating_mode = HEATING_MODE_ECO
    climate.heating_curve._last_heating_curve_value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 42.44))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 55.0))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 35.0))

    assert climate.requested_setpoint == 42.4


def test_requested_setpoint_uses_secondary_and_cap(monkeypatch, climate):
    climate._heating_mode = HEATING_MODE_COMFORT
    climate.heating_curve._last_heating_curve_value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 40.2))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 45.6))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 43.1))

    assert climate.requested_setpoint == 43.1


async def test_async_control_pid_updates_heating_curve_value(climate):
    climate.hass.states.async_set("sensor.test_inside_sensor", "20")
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")
    climate._target_temperature = 21.0

    await climate.async_control_pid()

    coefficient = float(OPTIONS_DEFAULTS[CONF_HEATING_CURVE_COEFFICIENT])
    expected_curve = HeatingCurve.calculate(21.0, 5.0)
    expected_value = round(climate.heating_curve.base_offset + ((coefficient / 4) * expected_curve), 1)

    assert climate.heating_curve.value == expected_value


async def test_async_control_setpoint_hvac_off_forces_minimum(climate):
    climate._hvac_mode = HVACMode.OFF
    climate._setpoint = 45.0

    await climate._async_control_setpoint()

    assert climate.setpoint == MINIMUM_SETPOINT
    assert climate._coordinator.setpoint == MINIMUM_SETPOINT


async def test_async_control_setpoint_holds_near_target(monkeypatch, climate):
    climate._hvac_mode = HVACMode.HEAT
    climate._setpoint = 50.0
    climate._overshoot_protection = False

    await climate._coordinator.async_set_heater_state(DeviceState.ON)
    await climate._coordinator.async_set_boiler_temperature(50.0 - NEAR_TARGET_MARGIN_CELSIUS + 0.1)

    monkeypatch.setattr(SatClimate, "requested_setpoint", property(lambda self: 40.0))
    await climate._async_control_setpoint()

    assert climate.setpoint == 50.0


async def test_async_control_setpoint_increase_applies_immediately(monkeypatch, climate):
    climate._hvac_mode = HVACMode.HEAT
    climate._setpoint = 50.0
    climate._overshoot_protection = False

    new_requested = 50.0 + INCREASE_STEP_THRESHOLD_CELSIUS + 0.1
    monkeypatch.setattr(SatClimate, "requested_setpoint", property(lambda self: new_requested))
    await climate._async_control_setpoint()

    assert climate.setpoint == new_requested
    assert climate._coordinator.setpoint == new_requested


async def test_async_control_setpoint_decrease_requires_persistence(monkeypatch, climate):
    climate._hvac_mode = HVACMode.HEAT
    climate._setpoint = 50.0
    climate._overshoot_protection = False

    requested_values = iter([45.0, 44.0, 43.0])

    monkeypatch.setattr(SatClimate, "requested_setpoint", property(lambda self: next(requested_values)))

    await climate._async_control_setpoint()
    assert climate.setpoint == 50.0

    await climate._async_control_setpoint()
    assert climate.setpoint == 50.0

    await climate._async_control_setpoint()
    assert climate.setpoint == 43.0
    assert climate._coordinator.setpoint == 43.0
