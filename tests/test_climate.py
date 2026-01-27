"""Tests focused on SAT climate setpoint and heating curve behavior."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util

from custom_components.sat.entry_data import SatConfig
from custom_components.sat.const import (
    CONF_HEATING_CURVE_COEFFICIENT,
    CONF_HEATING_MODE,
    CONF_SENSOR_MAX_VALUE_AGE,
    CONF_HEATING_SYSTEM,
    HeatingMode,
    HeatingSystem,
    MINIMUM_SETPOINT,
    OPTIONS_DEFAULTS,
)
from custom_components.sat.heating_curve import HeatingCurve
from custom_components.sat.manufacturer import ManufacturerFactory
from custom_components.sat.pid import PID

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
    climate._coordinator._manufacturer = ManufacturerFactory.resolve_by_name(new_config.manufacturer) if new_config.manufacturer else None


def test_requested_setpoint_without_heating_curve(climate):
    assert climate.heating_curve.value is None
    assert climate.requested_setpoint == MINIMUM_SETPOINT


def test_requested_setpoint_eco_mode_ignores_secondary_and_caps(monkeypatch, climate):
    _update_climate_config(climate, options={CONF_HEATING_MODE: HeatingMode.ECO})
    climate.heating_curve._value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 42.44))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 55.0))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 35.0))

    assert climate.requested_setpoint == 42.4


def test_requested_setpoint_uses_secondary_and_cap(monkeypatch, climate):
    _update_climate_config(climate, options={CONF_HEATING_MODE: HeatingMode.COMFORT})
    climate.heating_curve._value = 30.0

    monkeypatch.setattr(PID, "output", property(lambda self: 40.2))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.output", property(lambda self: 45.6))
    monkeypatch.setattr("custom_components.sat.area.Areas._PIDs.overshoot_cap", property(lambda self: 43.1))

    assert climate.requested_setpoint == 43.1


def test_update_heating_curves_updates_value(climate):
    climate.hass.states.async_set("sensor.test_inside_sensor", "20")
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")
    climate._target_temperature = 21.0

    climate._update_heating_curves()

    base_offset = HeatingSystem.RADIATORS.base_offset
    coefficient = float(OPTIONS_DEFAULTS[CONF_HEATING_CURVE_COEFFICIENT])
    expected_curve = HeatingCurve.calculate(21.0, 5.0)
    expected_value = round(base_offset + ((coefficient / 4) * expected_curve), 1)

    assert climate.heating_curve.value == expected_value


def test_control_pid_resets_on_stale_inside_sensor(monkeypatch, climate):
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

    climate.control_pid()

    assert reset_called["value"] is True


async def test_control_loop_skips_when_hvac_off(monkeypatch, climate):
    climate.hass.states.async_set("sensor.test_outside_sensor", "5")
    climate._hvac_mode = HVACMode.OFF

    update_mock = AsyncMock()
    monkeypatch.setattr(climate._heating_control, "update", update_mock)

    await climate.async_control_heating_loop()

    update_mock.assert_not_called()
