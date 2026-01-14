"""Tests focused on coordinator-level control overrides."""

import pytest

from custom_components.sat.boiler import BoilerControlIntent
from custom_components.sat.const import (
    CONF_MINIMUM_SETPOINT,
    CONF_OVERSHOOT_PROTECTION,
    CONF_HEATING_SYSTEM,
    HeatingSystem,
    OPTIONS_DEFAULTS, CONF_MODULATION_SUPPRESSION_DELAY_SECONDS, CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS,
)
from custom_components.sat.entry_data import SatConfig
from custom_components.sat.helpers import timestamp
from custom_components.sat.manufacturer import ManufacturerFactory
from custom_components.sat.pwm import PWMState
from custom_components.sat.types import DeviceState, PWMStatus

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
            CONF_MINIMUM_SETPOINT: 40.0,
            CONF_OVERSHOOT_PROTECTION: True,
            CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
        },
        options=options,
    )
    coordinator._config = config
    coordinator._manufacturer = ManufacturerFactory.resolve_by_name(config.manufacturer)


def _pwm_state(status: PWMStatus) -> PWMState:
    return PWMState(
        enabled=True,
        status=status,
        duty_cycle=None,
        last_duty_cycle_percentage=None,
    )


async def test_pwm_suppression_applied(coordinator):
    _update_coordinator_config(coordinator)

    coordinator._control_pwm_state = _pwm_state(PWMStatus.ON)
    await coordinator.async_set_heater_state(DeviceState.ON)
    await coordinator.async_set_boiler_temperature(50.0)
    coordinator._boiler._last_flame_on_at = timestamp() - (OPTIONS_DEFAULTS[CONF_MODULATION_SUPPRESSION_DELAY_SECONDS] + 1)

    coordinator.set_control_intent(BoilerControlIntent(setpoint=60.0, relative_modulation=None))
    await coordinator.async_control_heating_loop()

    assert coordinator.setpoint == 49.0


async def test_pwm_flame_off_return_offset(monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: 30.0))

    coordinator._control_pwm_state = _pwm_state(PWMStatus.ON)
    await coordinator.async_set_heater_state(DeviceState.OFF)

    coordinator.set_control_intent(BoilerControlIntent(setpoint=40.0, relative_modulation=None))
    await coordinator.async_control_heating_loop()

    assert coordinator.setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]


async def test_flame_off_setpoint_held_until_suppression_delay(monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: 30.0))

    coordinator._control_pwm_state = _pwm_state(PWMStatus.ON)
    await coordinator.async_set_heater_state(DeviceState.OFF)

    coordinator.set_control_intent(BoilerControlIntent(setpoint=40.0, relative_modulation=None))
    await coordinator.async_control_heating_loop()

    assert coordinator.setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]

    await coordinator.async_set_heater_state(DeviceState.ON)
    await coordinator.async_set_boiler_temperature(50.0)
    coordinator._boiler._last_flame_on_at = timestamp() - (OPTIONS_DEFAULTS[CONF_MODULATION_SUPPRESSION_DELAY_SECONDS] - 1)

    await coordinator.async_control_heating_loop()

    assert coordinator.setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]
