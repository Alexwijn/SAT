"""Tests focused on heating control behavior."""

import pytest
from homeassistant.components.climate import HVACMode

from custom_components.sat.const import (
    CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS,
    CONF_HEATING_SYSTEM,
    CONF_MINIMUM_SETPOINT,
    CONF_MODE,
    CONF_MODULATION_SUPPRESSION_DELAY_SECONDS,
    CONF_OVERSHOOT_PROTECTION,
    HeatingSystem,
    MINIMUM_SETPOINT,
    OPTIONS_DEFAULTS,
)
from custom_components.sat.entry_data import SatConfig, SatMode
from custom_components.sat.helpers import timestamp
from custom_components.sat.heating_control import (
    DECREASE_STEP_THRESHOLD_CELSIUS,
    INCREASE_STEP_THRESHOLD_CELSIUS,
    NEAR_TARGET_MARGIN_CELSIUS,
    SatHeatingControl,
)
from custom_components.sat.manufacturer import ManufacturerFactory
from custom_components.sat.types import HeaterState, PWMStatus

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


def _update_coordinator_config(coordinator) -> None:
    options = {
        **OPTIONS_DEFAULTS,
    }
    config = SatConfig(
        entry_id="test",
        data={
            CONF_MODE: SatMode.FAKE.value,
            CONF_MINIMUM_SETPOINT: 40.0,
            CONF_OVERSHOOT_PROTECTION: True,
            CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
        },
        options=options,
    )
    coordinator._config = config
    coordinator._manufacturer = ManufacturerFactory.resolve_by_name(config.manufacturer)


def _enable_pwm(heating_control: SatHeatingControl, status: PWMStatus) -> None:
    if heating_control.pwm is None:
        return

    heating_control.pwm._enabled = True
    heating_control.pwm._status = status


def test_hvac_off_forces_minimum(heating_control):
    heating_control._setpoint = 45.0

    heating_control.update(HVACMode.OFF, 45.0, None)

    assert heating_control.setpoint == MINIMUM_SETPOINT


async def test_holds_near_target(heating_control, coordinator):
    heating_control._setpoint = 50.0

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(50.0 - NEAR_TARGET_MARGIN_CELSIUS + 0.1)

    heating_control.update(HVACMode.HEAT, 40.0, None)

    assert heating_control.setpoint == 50.0


def test_increase_applies_immediately(heating_control):
    heating_control._setpoint = 50.0

    new_requested = 50.0 + INCREASE_STEP_THRESHOLD_CELSIUS + 0.1
    heating_control.update(HVACMode.HEAT, new_requested, None)

    assert heating_control.setpoint == new_requested


def test_increase_applies_at_threshold(heating_control):
    heating_control._setpoint = 50.0

    new_requested = 50.0 + INCREASE_STEP_THRESHOLD_CELSIUS
    heating_control.update(HVACMode.HEAT, new_requested, None)

    assert heating_control.setpoint == new_requested


async def test_decrease_applies_below_target(heating_control, coordinator):
    heating_control._setpoint = 50.0

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(40.0)

    heating_control.update(HVACMode.HEAT, 45.0, None)

    assert heating_control.setpoint == 45.0


async def test_decrease_applies_at_threshold_below_target(heating_control, coordinator):
    heating_control._setpoint = 50.0

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(40.0)

    new_requested = 50.0 - DECREASE_STEP_THRESHOLD_CELSIUS
    heating_control.update(HVACMode.HEAT, new_requested, None)

    assert heating_control.setpoint == new_requested


async def test_pwm_suppression_applied(hass, coordinator):
    _update_coordinator_config(coordinator)

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(50.0)
    heating_control._device_tracker._last_flame_on_at = timestamp() - (
        OPTIONS_DEFAULTS[CONF_MODULATION_SUPPRESSION_DELAY_SECONDS] + 1
    )

    heating_control.update(HVACMode.HEAT, 40.0, None)

    assert heating_control.setpoint == 49.0


async def test_pwm_flame_off_return_offset(hass, monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: 30.0))

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)

    await coordinator.async_set_heater_state(HeaterState.OFF)

    heating_control.update(HVACMode.HEAT, 40.0, None)

    assert heating_control.setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]


async def test_flame_off_setpoint_held_until_suppression_delay(hass, monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: 30.0))

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)

    await coordinator.async_set_heater_state(HeaterState.OFF)

    heating_control.update(HVACMode.HEAT, 40.0, None)

    assert heating_control.setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(50.0)
    heating_control._device_tracker._last_flame_on_at = timestamp() - (
        OPTIONS_DEFAULTS[CONF_MODULATION_SUPPRESSION_DELAY_SECONDS] - 1
    )

    heating_control.update(HVACMode.HEAT, 40.0, None)

    assert heating_control.setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]
