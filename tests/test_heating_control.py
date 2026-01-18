"""Tests focused on heating control behavior."""

import pytest
from homeassistant.components.climate import HVACMode

from custom_components.sat.const import (
    CONF_DYNAMIC_MINIMUM_SETPOINT,
    CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS,
    CONF_FLOW_SETPOINT_OFFSET_CELSIUS,
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
    HeatingDemand,
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
        CONF_DYNAMIC_MINIMUM_SETPOINT: True,
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
    heating_control._pwm._enabled = True
    heating_control._pwm._status = status


def _make_demand(requested_setpoint: float, hvac_mode: HVACMode = HVACMode.HEAT, outside_temperature: float = 10.0) -> HeatingDemand:
    return HeatingDemand(
        timestamp=timestamp(),
        hvac_mode=hvac_mode,
        requested_setpoint=requested_setpoint,
        outside_temperature=outside_temperature,
    )


async def test_hvac_off_forces_minimum(heating_control):
    heating_control._control_setpoint = 45.0

    await heating_control.update(_make_demand(45.0, hvac_mode=HVACMode.OFF))

    assert heating_control.control_setpoint == MINIMUM_SETPOINT


async def test_continuous_uses_requested_without_boiler_temperature(heating_control):
    await heating_control.update(_make_demand(42.0))

    assert heating_control.control_setpoint == 42.0


async def test_continuous_follows_requested_at_or_above_boiler_temperature(heating_control, coordinator):
    await coordinator.async_set_boiler_temperature(35.0)

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 40.0


async def test_continuous_clamps_below_boiler_temperature(heating_control, coordinator):
    await coordinator.async_set_boiler_temperature(55.0)

    await heating_control.update(_make_demand(40.0))

    expected = 55.0 - OPTIONS_DEFAULTS[CONF_FLOW_SETPOINT_OFFSET_CELSIUS]
    assert heating_control.control_setpoint == expected


async def test_continuous_allows_requested_above_offset(heating_control, coordinator):
    await coordinator.async_set_boiler_temperature(55.0)

    requested = 55.0 - OPTIONS_DEFAULTS[CONF_FLOW_SETPOINT_OFFSET_CELSIUS] + 0.5
    await heating_control.update(_make_demand(requested))

    assert heating_control.control_setpoint == requested


async def test_pwm_suppression_applied(hass, coordinator, monkeypatch):
    _update_coordinator_config(coordinator)

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(50.0)
    heating_control._device_tracker._last_flame_on_at = timestamp() - (
        OPTIONS_DEFAULTS[CONF_MODULATION_SUPPRESSION_DELAY_SECONDS] + 1
    )

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 49.0


async def test_pwm_flame_off_return_offset(hass, monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: 30.0))

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.OFF)

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]


async def test_flame_off_setpoint_held_until_suppression_delay(hass, monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: 30.0))

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.OFF)

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(50.0)
    heating_control._device_tracker._last_flame_on_at = timestamp() - (
        OPTIONS_DEFAULTS[CONF_MODULATION_SUPPRESSION_DELAY_SECONDS] - 1
    )

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]
