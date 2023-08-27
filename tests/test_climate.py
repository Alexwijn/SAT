"""The tests for the climate component."""

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.template import DOMAIN as TEMPLATE_DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.climate import SatClimate
from custom_components.sat.const import *
from custom_components.sat.fake import SatFakeCoordinator


@pytest.mark.parametrize(*[
    "domains, data, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
                CONF_HEATING_CURVE_COEFFICIENT: 1.8
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
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
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    assert climate.setpoint == 57
    assert climate.heating_curve.value == 32.6

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 36.17
    assert climate.pwm.duty_cycle == (325, 574)


@pytest.mark.parametrize(*[
    "domains, data, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 58,
                CONF_MAXIMUM_SETPOINT: 75,
                CONF_HEATING_CURVE_COEFFICIENT: 1.3
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
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
    await climate.async_set_target_temperature(19.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    assert climate.setpoint == 58
    assert climate.heating_curve.value == 30.1
    assert climate.requested_setpoint == 30.597

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 11.03
    assert climate.pwm.duty_cycle == (180, 1452)


@pytest.mark.parametrize(*[
    "domains, data, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 41,
                CONF_MAXIMUM_SETPOINT: 75,
                CONF_HEATING_CURVE_COEFFICIENT: 0.9
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
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
    await climate.async_set_target_temperature(20.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    assert climate.setpoint == 41
    assert climate.heating_curve.value == 32.1
    assert climate.requested_setpoint == 37.397

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 73.89
    assert climate.pwm.duty_cycle == (665, 234)
