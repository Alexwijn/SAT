"""Tests for the PWM controller."""

from typing import Optional

import pytest
from homeassistant.core import State

from custom_components.sat.boiler import BoilerState
from custom_components.sat.const import HEATING_SYSTEM_RADIATORS
from custom_components.sat.heating_curve import HeatingCurve
from custom_components.sat.pwm import PWM, PWMConfig
from custom_components.sat.types import PWMStatus


def _make_boiler_state(
    *,
    flame_active: bool = False,
    hot_water_active: bool = False,
    flame_on_since: Optional[float] = None,
    setpoint: Optional[float] = 40.0,
    flow_temperature: Optional[float] = 50.0,
) -> BoilerState:
    return BoilerState(
        flame_active=flame_active,
        central_heating=True,
        hot_water_active=hot_water_active,
        flame_on_since=flame_on_since,
        flame_off_since=None,
        setpoint=setpoint,
        flow_temperature=flow_temperature,
        return_temperature=40.0,
        relative_modulation_level=None,
        max_modulation_level=100,
        modulation_reliable=True
    )


@pytest.fixture
def heating_curve() -> HeatingCurve:
    curve = HeatingCurve(HEATING_SYSTEM_RADIATORS, coefficient=1.0)
    curve.update(20.0, 20.0)
    return curve


@pytest.fixture
def pwm(heating_curve: HeatingCurve) -> PWM:
    return PWM(PWMConfig(maximum_cycles=4, maximum_cycle_time=900), heating_curve)


def test_initial_state_and_restore(pwm: PWM):
    assert pwm.enabled is False
    assert pwm.status is PWMStatus.IDLE
    assert pwm.state.duty_cycle is None
    assert pwm.state.last_duty_cycle_percentage is None

    state = State("sensor.pwm", "0", attributes={"pulse_width_modulation_enabled": True})
    pwm.restore(state)
    assert pwm.enabled is True


def test_update_missing_values_sets_idle(pwm: PWM, caplog):
    boiler_state = _make_boiler_state()

    pwm._heating_curve._last_heating_curve_value = None
    pwm.update(boiler_state, 40.0, 123.0)

    assert pwm.enabled is True
    assert pwm.status is PWMStatus.IDLE
    assert "PWM turned off due missing values" in caplog.text


@pytest.mark.parametrize(
    "ratio, flame_active, hot_water_active, expected, tolerance",
    [
        (0.05, False, False, (0, 1800), 0),
        (0.05, True, False, (180, 1620), 0),
        (0.15, False, False, (180, 1020), 0),
        (0.50, False, False, (450, 450), 1),
        (0.85, False, False, (1020, 180), 1),
        (0.95, False, False, (1800, 0), 0),
    ],
)
def test_calculate_duty_cycle_ranges(pwm: PWM, ratio, flame_active, hot_water_active, expected, tolerance):
    boiler_temperature = 50.0
    pwm._effective_on_temperature = boiler_temperature

    base_offset = pwm._heating_curve.base_offset
    setpoint = base_offset + ratio * (boiler_temperature - base_offset)

    boiler_state = _make_boiler_state(flame_active=flame_active, hot_water_active=hot_water_active)
    on_time, off_time = pwm._calculate_duty_cycle(setpoint, boiler_state)

    if tolerance:
        assert on_time == pytest.approx(expected[0], abs=tolerance)
        assert off_time == pytest.approx(expected[1], abs=tolerance)
    else:
        assert (on_time, off_time) == expected


def test_update_transitions_and_cycle_limit(pwm: PWM):
    base_offset = pwm._heating_curve.base_offset
    setpoint = base_offset + 0.5 * (50.0 - base_offset)
    boiler_state = _make_boiler_state(flow_temperature=50.0)

    pwm._last_update = 0.0
    pwm.update(boiler_state, setpoint, 0.0)
    assert pwm.status is PWMStatus.ON
    assert pwm._current_cycle == 1

    pwm.update(boiler_state, setpoint, 450.0)
    assert pwm.status is PWMStatus.OFF

    pwm._current_cycle = pwm._config.maximum_cycles
    pwm.update(boiler_state, setpoint, 900.0)
    assert pwm.status is PWMStatus.OFF
    assert pwm._current_cycle == pwm._config.maximum_cycles


def test_cycle_count_resets_after_rolling_hour(pwm: PWM):
    pwm._status = PWMStatus.IDLE
    pwm._current_cycle = 4
    pwm._first_duty_cycle_start = 0.0
    pwm._last_update = 0.0

    base_offset = pwm._heating_curve.base_offset
    setpoint = base_offset + 0.05 * (50.0 - base_offset)
    boiler_state = _make_boiler_state(flow_temperature=50.0)

    pwm.update(boiler_state, setpoint, 3701.0)

    assert pwm._current_cycle == 0
    assert pwm._first_duty_cycle_start == 3701.0
    assert pwm.status is PWMStatus.OFF


def test_effective_temperature_updates_when_flame_stable(pwm: PWM):
    base_offset = pwm._heating_curve.base_offset
    setpoint = base_offset + 0.5 * (50.0 - base_offset)

    pwm._status = PWMStatus.ON
    pwm._last_update = 100.0
    pwm._effective_on_temperature = 40.0
    pwm._first_duty_cycle_start = 100.0

    boiler_state = _make_boiler_state(
        flame_active=True,
        flame_on_since=100.0 - 40.0,
        flow_temperature=50.0,
    )

    pwm.update(boiler_state, setpoint, 110.0)

    assert pwm.status is PWMStatus.ON
    assert pwm._effective_on_temperature == pytest.approx(43.0)


def test_disable_resets_state(pwm: PWM):
    pwm._enabled = True
    pwm._status = PWMStatus.ON
    pwm._duty_cycle = (100, 100)
    pwm._last_duty_cycle_percentage = 0.5

    pwm.disable()

    assert pwm.enabled is False
    assert pwm.status is PWMStatus.IDLE
    assert pwm.state.duty_cycle is None
    assert pwm.state.last_duty_cycle_percentage is None
