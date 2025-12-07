"""The tests for the climate component."""

import pytest
from homeassistant.components import template, sensor
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.climate import SatClimate
from custom_components.sat.const import *
from custom_components.sat.fake import SatFakeCoordinator


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(template.DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                template.DOMAIN: [
                    {
                        sensor.DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 20.9 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 9.9 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_scenario_1(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    await climate.async_control_pid()
    await climate.async_control_heating_loop()

    assert climate.setpoint == 57
    assert climate.max_error.value == 0.1
    assert climate.heating_curve.value == 32.2

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.state.duty_cycle == (277, 922)
    assert climate.pwm.state.last_duty_cycle_percentage == 23.15


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(template.DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 58,
                CONF_MAXIMUM_SETPOINT: 75
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.3,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                template.DOMAIN: [
                    {
                        sensor.DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 18.99 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 11.1 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_scenario_2(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(58)
    await climate.async_set_target_temperature(19.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    await climate.async_control_pid()
    await climate.async_control_heating_loop()

    assert climate.setpoint == 10
    assert climate.max_error.value == 0.01
    assert climate.heating_curve.value == 27.8
    assert climate.requested_setpoint == 27.9

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.state.duty_cycle == (0, 2400)
    assert climate.pwm.state.last_duty_cycle_percentage == 2.27


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(template.DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 41,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 0.9,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                template.DOMAIN: [
                    {
                        sensor.DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 19.9 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ -2.2 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_scenario_3(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(41)
    await climate.async_set_target_temperature(20.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    await climate.async_control_pid()
    await climate.async_control_heating_loop()

    assert climate.setpoint == 41.0
    assert climate.max_error.value == 0.1
    assert climate.heating_curve.value == 32.5
    assert climate.requested_setpoint == 33.5

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.state.duty_cycle == (547, 652)
    assert climate.pwm.state.last_duty_cycle_percentage == 45.65
