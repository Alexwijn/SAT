"""The tests for the climate component."""

import pytest
from time import monotonic
from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.template import DOMAIN as TEMPLATE_DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.climate import SatClimate
from custom_components.sat.const import *
from custom_components.sat.coordinator import DeviceState
from custom_components.sat.fake import SatFakeCoordinator


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
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
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate.setpoint == 57
    assert climate.heating_curve.value == 32.2

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 23.83
    assert climate.pwm.duty_cycle == (285, 914)


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
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
    await coordinator.async_set_boiler_temperature(58)
    await climate.async_set_target_temperature(19.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate.setpoint == 10
    assert climate.heating_curve.value == 27.8
    assert climate.requested_setpoint == 28.0

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 2.6
    assert climate.pwm.duty_cycle == (0, 2400)


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
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
    await coordinator.async_set_boiler_temperature(41)
    await climate.async_set_target_temperature(20.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate.setpoint == 41.0
    assert climate.heating_curve.value == 32.5
    assert climate.requested_setpoint == 34.6

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 53.62
    assert climate.pwm.duty_cycle == (643, 556)


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
                CONF_PUMP_POST_CIRCULATION_TIME: "00:03:00",
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
async def test_pump_post_circulation_pwm_off_keeps_heater_on(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    """Test that PWM OFF keeps ch_enable=true when post-circulation is configured."""
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate._is_opentherm_mode
    assert climate._pump_post_circulation_time == 180


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
                CONF_PUMP_POST_CIRCULATION_TIME: "00:03:00",
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
async def test_pump_post_circulation_hvac_off_starts_timer(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    """Test that HVAC OFF triggers post-circulation when configured and device is active."""
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    # Device should be active after heating
    assert coordinator.device_active

    # Switch to OFF - should start post-circulation instead of immediately turning off
    await climate.async_set_hvac_mode(HVACMode.OFF)

    assert climate._heating_demand_ended_at is not None
    assert climate._pump_post_circulation_active
    # Device should still be active (not turned off immediately)
    assert coordinator.device_active


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
                CONF_PUMP_POST_CIRCULATION_TIME: "00:03:00",
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
async def test_pump_post_circulation_timer_expires(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    """Test that post-circulation timer expires and pump turns off."""
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    # Switch to OFF
    await climate.async_set_hvac_mode(HVACMode.OFF)
    assert climate._pump_post_circulation_active

    # Simulate timer expiry by moving the timestamp back
    climate._heating_demand_ended_at = monotonic() - 200
    assert not climate._pump_post_circulation_active


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
                CONF_PUMP_POST_CIRCULATION_TIME: "00:03:00",
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
async def test_pump_post_circulation_excluded_for_switch_mode(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    """Test that MODE_SWITCH is excluded from post-circulation behavior."""
    # Override the mode to simulate switch mode
    climate._mode = MODE_SWITCH
    assert not climate._is_opentherm_mode
    assert climate._pump_post_circulation_time == 180

    # Even with timer configured, switch mode should not use post-circulation
    await coordinator.async_set_heater_state(DeviceState.ON)
    assert coordinator.device_active

    await climate.async_set_hvac_mode(HVACMode.OFF)

    # For switch mode, heater should be turned off immediately
    assert climate._heating_demand_ended_at is None
    assert not coordinator.device_active


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
                CONF_PUMP_POST_CIRCULATION_TIME: "00:03:00",
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
async def test_pump_post_circulation_off_to_heat_clears_timer(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    """Test that switching from OFF back to HEAT clears the post-circulation timer."""
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    # Switch to OFF - starts post-circulation
    await climate.async_set_hvac_mode(HVACMode.OFF)
    assert climate._heating_demand_ended_at is not None
    assert climate._pump_post_circulation_active

    # Switch back to HEAT - should clear the timer
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    assert climate._heating_demand_ended_at is None


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
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
async def test_pump_post_circulation_disabled_by_default(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    """Test that post-circulation is disabled when not configured (default 00:00:00)."""
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate._pump_post_circulation_time == 0
    assert not climate._pump_post_circulation_active

    # Device should be active after heating
    assert coordinator.device_active

    # Switch to OFF - should turn off immediately (no post-circulation)
    await climate.async_set_hvac_mode(HVACMode.OFF)
    assert climate._heating_demand_ended_at is None
    assert not coordinator.device_active
