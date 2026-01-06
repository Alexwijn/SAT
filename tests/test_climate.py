"""Tests focused on SAT climate setpoint and heating curve behavior."""

from datetime import timedelta
from itertools import chain, repeat

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util

from custom_components.sat.climate import (
    INCREASE_STEP_THRESHOLD_CELSIUS,
    NEAR_TARGET_MARGIN_CELSIUS,
    SatClimate,
)
from custom_components.sat.entry_data import SatConfig
from custom_components.sat.const import (
    CONF_DYNAMIC_MINIMUM_SETPOINT,
    CONF_HEATING_CURVE_COEFFICIENT,
    CONF_HEATING_MODE,
    CONF_MINIMUM_SETPOINT,
    CONF_OVERSHOOT_PROTECTION,
    CONF_SENSOR_MAX_VALUE_AGE,
    CONF_HEATING_SYSTEM,
    HeatingMode,
    HeatingSystem,
    MINIMUM_SETPOINT,
    OPTIONS_DEFAULTS,
    PWM_DISABLE_MARGIN_CELSIUS,
    PWM_ENABLE_MARGIN_CELSIUS,
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
                CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
            },
            {},
            {},
        ),
    ],
)


def _update_climate_config(climate, *, data=None, options=None) -> None:
    current_data = dict(climate._config.data)
    current_options = dict(climate._config.options)

    if data:
        current_data.update(data)

    if options:
        current_options.update(options)

    new_config = SatConfig(climate._config.entry_id, current_data, {**OPTIONS_DEFAULTS, **current_options})
    climate._config = new_config
    climate._coordinator._config = new_config


def test_requested_setpoint_without_heating_curve(climate):
    assert climate.heating_curve.value is None
    assert climate.requested_setpoint == MINIMUM_SETPOINT


def test_requested_setpoint_eco_mode_ignores_secondary_and_caps(monkeypatch, climate):
    _update_climate_config(climate, options={CONF_HEATING_MODE: HeatingMode.ECO})
    climate.heating_curve._last_heating_curve_value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 42.44))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 55.0))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 35.0))

    assert climate.requested_setpoint == 42.4


def test_requested_setpoint_uses_secondary_and_cap(monkeypatch, climate):
    _update_climate_config(climate, options={CONF_HEATING_MODE: HeatingMode.COMFORT})
    climate.heating_curve._last_heating_curve_value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 40.2))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 45.6))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 43.1))

    assert climate.requested_setpoint == 43.1


async def test_async_control_pid_updates_heating_curve_value(climate):
    climate.hass.states.async_set("sensor.test_inside_sensor", "20")
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")
    climate._target_temperature = 21.0

    climate.heating_curve.update(climate.target_temperature, climate.current_outside_temperature)

    await climate.async_control_pid()

    coefficient = float(OPTIONS_DEFAULTS[CONF_HEATING_CURVE_COEFFICIENT])
    expected_curve = HeatingCurve.calculate(21.0, 5.0)
    expected_value = round(climate.heating_curve.base_offset + ((coefficient / 4) * expected_curve), 1)

    assert climate.heating_curve.value == expected_value


async def test_async_control_pid_resets_on_stale_inside_sensor(monkeypatch, climate):
    _update_climate_config(climate, options={CONF_SENSOR_MAX_VALUE_AGE: "00:01:00"})
    climate._target_temperature = 21.0

    now = dt_util.utcnow()
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    climate.hass.states.async_set("sensor.test_inside_sensor", "20")
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")

    monkeypatch.setattr(dt_util, "utcnow", lambda: now + timedelta(seconds=120))

    reset_called = {"value": False}

    def _reset(self):
        reset_called["value"] = True

    monkeypatch.setattr(PID, "reset", _reset)

    await climate.async_control_pid()

    assert reset_called["value"] is True
    assert climate.heating_curve.value is None


async def test_async_control_setpoint_hvac_off_forces_minimum(climate):
    climate._hvac_mode = HVACMode.OFF
    climate._setpoint = 45.0

    await climate._async_control_setpoint()

    assert climate.setpoint == MINIMUM_SETPOINT
    assert climate._coordinator.setpoint == MINIMUM_SETPOINT


async def test_async_control_setpoint_holds_near_target(monkeypatch, climate):
    climate._hvac_mode = HVACMode.HEAT
    climate._setpoint = 50.0
    _update_climate_config(climate, data={CONF_OVERSHOOT_PROTECTION: False})

    await climate._coordinator.async_set_heater_state(DeviceState.ON)
    await climate._coordinator.async_set_boiler_temperature(50.0 - NEAR_TARGET_MARGIN_CELSIUS + 0.1)

    monkeypatch.setattr(SatClimate, "requested_setpoint", property(lambda self: 40.0))
    await climate._async_control_setpoint()

    assert climate.setpoint == 50.0


async def test_async_control_setpoint_increase_applies_immediately(monkeypatch, climate):
    climate._hvac_mode = HVACMode.HEAT
    climate._setpoint = 50.0
    _update_climate_config(climate, data={CONF_OVERSHOOT_PROTECTION: False})

    new_requested = 50.0 + INCREASE_STEP_THRESHOLD_CELSIUS + 0.1
    monkeypatch.setattr(SatClimate, "requested_setpoint", property(lambda self: new_requested))
    await climate._async_control_setpoint()

    assert climate.setpoint == new_requested
    assert climate._coordinator.setpoint == new_requested


async def test_async_control_setpoint_decrease_requires_persistence(monkeypatch, climate):
    climate._hvac_mode = HVACMode.HEAT
    climate._setpoint = 50.0
    _update_climate_config(climate, data={CONF_OVERSHOOT_PROTECTION: False})
    await climate._coordinator.async_set_heater_state(DeviceState.ON)

    requested_values = iter(chain([45.0, 44.0, 43.0], repeat(43.0)))

    monkeypatch.setattr(SatClimate, "requested_setpoint", property(lambda self: next(requested_values)))

    await climate._async_control_setpoint()
    assert climate.setpoint == 50.0

    await climate._async_control_setpoint()
    assert climate.setpoint == 50.0

    await climate._async_control_setpoint()
    assert climate.setpoint == 43.0
    assert climate._coordinator.setpoint == 43.0


def test_pwm_disabled_without_setpoint(climate):
    climate._setpoint = None
    climate._coordinator.config.supports_setpoint_management = False

    assert climate.pulse_width_modulation_enabled is False


def test_pwm_forced_without_setpoint_management(climate):
    climate._setpoint = 45.0
    _update_climate_config(climate, data={CONF_OVERSHOOT_PROTECTION: False})
    climate._coordinator.config.supports_setpoint_management = False

    assert climate.pulse_width_modulation_enabled is True


def test_pwm_static_minimum_setpoint_deadband(monkeypatch, climate):
    climate._setpoint = 41.0
    _update_climate_config(
        climate,
        data={CONF_MINIMUM_SETPOINT: 40.0, CONF_OVERSHOOT_PROTECTION: True},
        options={CONF_DYNAMIC_MINIMUM_SETPOINT: False},
    )
    delta = (PWM_ENABLE_MARGIN_CELSIUS + PWM_DISABLE_MARGIN_CELSIUS) / 2

    monkeypatch.setattr(
        SatClimate,
        "requested_setpoint",
        property(lambda self: self._coordinator.minimum_setpoint + delta),
    )

    climate.pwm._enabled = False
    assert climate.pulse_width_modulation_enabled is False

    climate.pwm._enabled = True
    assert climate.pulse_width_modulation_enabled is True


@pytest.mark.parametrize(
    ("delta", "pwm_enabled", "expected"),
    [
        (PWM_ENABLE_MARGIN_CELSIUS - 0.1, False, True),
        (PWM_DISABLE_MARGIN_CELSIUS + 0.1, True, False),
        ((PWM_ENABLE_MARGIN_CELSIUS + PWM_DISABLE_MARGIN_CELSIUS) / 2, True, True),
        ((PWM_ENABLE_MARGIN_CELSIUS + PWM_DISABLE_MARGIN_CELSIUS) / 2, False, False),
    ],
)
def test_pwm_dynamic_minimum_setpoint_hysteresis(monkeypatch, climate, delta, pwm_enabled, expected):
    climate._setpoint = 45.0
    _update_climate_config(
        climate,
        data={CONF_OVERSHOOT_PROTECTION: True},
        options={CONF_DYNAMIC_MINIMUM_SETPOINT: True},
    )
    climate.minimum_setpoint._value = 40.0
    climate._coordinator.config.supports_setpoint_management = True
    climate._coordinator.config.supports_relative_modulation_management = True
    climate._coordinator._boiler._modulation_reliable = True
    climate._coordinator._device_state = DeviceState.ON

    monkeypatch.setattr(
        SatClimate,
        "requested_setpoint",
        property(lambda self: self.minimum_setpoint.value + delta),
    )

    climate.pwm._enabled = pwm_enabled
    assert climate.pulse_width_modulation_enabled is expected
