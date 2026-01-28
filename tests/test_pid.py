"""Tests for the PID controller."""

from datetime import datetime

import pytest

from custom_components.sat.const import DEADBAND
from custom_components.sat.entry_data import PidConfig
from custom_components.sat.heating_curve import HeatingCurve
from custom_components.sat.pid import (
    DERIVATIVE_ALPHA1,
    DERIVATIVE_ALPHA2,
    DERIVATIVE_DECAY,
    DERIVATIVE_RAW_CAP,
    PID,
    DERIVATIVE_MAX_INTERVAL,
    INTEGRAL_MAX_INTERVAL,
)
from custom_components.sat.temperature.state import TemperatureState
from custom_components.sat.types import HeatingSystem


def _state_for_error(error, timestamp_value, current=20.0):
    setpoint = current + error
    timestamp_dt = datetime.fromtimestamp(timestamp_value)
    return TemperatureState(
        entity_id="climate.test",
        current=current,
        setpoint=setpoint,
        last_reported=timestamp_dt,
        last_updated=timestamp_dt,
        last_changed=timestamp_dt,
    )


def _pid_config(
        *,
        proportional: float = 1.0,
        integral: float = 0.5,
        derivative: float = 0.1,
        automatic_gains: bool = False,
        automatic_gains_value: float = 0.0,
        heating_curve_coefficient: float = 2.0,
) -> PidConfig:
    return PidConfig(
        integral=integral,
        derivative=derivative,
        proportional=proportional,
        automatic_gains=automatic_gains,
        automatic_gains_value=automatic_gains_value,
        heating_curve_coefficient=heating_curve_coefficient,
    )


def _make_pid(
        heating_system: HeatingSystem,
        *,
        config: PidConfig,
        heating_curve_value: float | None = None,
) -> PID:
    heating_curve = HeatingCurve(heating_system, config.heating_curve_coefficient)
    if heating_curve_value is not None:
        heating_curve._value = heating_curve_value

    return PID(heating_system=heating_system, config=config, heating_curve=heating_curve)


def _set_heating_curve_value(pid: PID, value: float) -> None:
    pid._heating_curve._value = value


def test_initial_state_and_availability():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(),
    )

    assert pid.available is False
    assert pid.integral == 0.0
    assert pid.raw_derivative == 0.0
    assert pid.derivative == 0.0
    assert pid.output == 0.0


def test_manual_gains_output_and_availability():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(
            proportional=2.0,
            integral=1.0,
            derivative=0.5,
        ),
    )

    _set_heating_curve_value(pid, 30.0)
    pid.update(_state_for_error(0.05, 0.0))
    pid.update(_state_for_error(0.05, 10.0))

    assert pid.available is True
    assert pid.kp == 2.0
    assert pid.ki == 1.0
    assert pid.kd == 0.5
    assert pid.proportional == 0.1
    assert pid.integral == 0.5
    assert pid.derivative == 0.0
    assert pid.output == 30.6


def test_automatic_gains_calculation():
    pid = _make_pid(
        HeatingSystem.UNDERFLOOR,
        config=_pid_config(
            automatic_gains=True,
            proportional=1.0,
            integral=1.0,
            derivative=1.0,
        ),
    )

    assert pid.kp == 0.0

    _set_heating_curve_value(pid, 40.0)
    pid.update(_state_for_error(0.05, 5.0))

    assert pid.kp == 20.0
    assert pid.ki == round(20.0 / 8400, 6)
    assert pid.kd == round(0.07 * 8400 * 20.0, 6)


def test_integral_timebase_reset_and_accumulation():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, integral=1.0, derivative=0.0),
    )

    _set_heating_curve_value(pid, 10.0)

    pid.update(_state_for_error(DEADBAND + 0.4, 10.0))
    assert pid.integral == 0.0

    pid.update(_state_for_error(DEADBAND / 2, 20.0))
    assert pid.integral == 0.0

    pid.update(_state_for_error(DEADBAND / 2, 30.0))
    assert pid.integral == 0.5


def test_integral_clamped_to_heating_curve():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, integral=1.0, derivative=0.0),
    )

    _set_heating_curve_value(pid, 0.5)
    pid.update(_state_for_error(DEADBAND, 0.0))
    pid.update(_state_for_error(DEADBAND, 10.0))

    assert pid.integral == 0.5


def test_integral_clamps_large_interval():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, integral=1.0, derivative=0.0),
    )

    _set_heating_curve_value(pid, 100.0)
    pid.update(_state_for_error(0.05, 0.0))
    state = _state_for_error(0.05, INTEGRAL_MAX_INTERVAL + 600.0)
    pid.update(state)

    expected = 0.05 * INTEGRAL_MAX_INTERVAL
    assert pid.integral == pytest.approx(expected, rel=1e-3)


def test_derivative_filtering_and_cap():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, proportional=0.0, integral=0.0, derivative=1.0),
    )

    _set_heating_curve_value(pid, 10.0)
    pid.update(_state_for_error(1.0, 10.0, current=10.0))
    pid.update(_state_for_error(1.0, 11.0, current=11.0))

    derivative = -(11.0 - 10.0) / 1.0
    expected_raw = DERIVATIVE_ALPHA2 * (DERIVATIVE_ALPHA1 * derivative)

    assert pid.raw_derivative == pytest.approx(round(expected_raw, 3), rel=1e-3)
    assert pid.derivative == pytest.approx(expected_raw, rel=1e-3)

    pid.update(_state_for_error(1.0, 12.0, current=1000.0))
    assert pid.raw_derivative == -DERIVATIVE_RAW_CAP


def test_derivative_freeze_in_deadband():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, proportional=0.0, integral=0.0, derivative=1.0),
    )

    _set_heating_curve_value(pid, 10.0)
    pid.update(_state_for_error(1.0, 10.0, current=10.0))
    pid._raw_derivative = 3.0

    pid.update(_state_for_error(DEADBAND / 2, 20.0, current=10.0))

    assert pid.raw_derivative == pytest.approx(3.0, rel=1e-3)


def test_derivative_uses_sensor_timing():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, proportional=0.0, integral=0.0, derivative=1.0),
    )

    _set_heating_curve_value(pid, 10.0)
    pid.update(_state_for_error(1.0, 100.0, current=10.0))
    pid.update(_state_for_error(1.0, 200.0, current=11.0))

    derivative = -(11.0 - 10.0) / 100.0
    expected_raw = DERIVATIVE_ALPHA2 * (DERIVATIVE_ALPHA1 * derivative)

    assert pid.raw_derivative == pytest.approx(round(expected_raw, 3), rel=1e-3)


def test_temperature_resolution_infers_small_deltas():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, proportional=0.0, integral=0.0, derivative=0.0),
    )

    _set_heating_curve_value(pid, 10.0)
    pid.update(_state_for_error(0.0, 0.0, current=20.0))
    pid.update(_state_for_error(0.0, 10.0, current=20.1))
    pid.update(_state_for_error(0.0, 20.0, current=20.2))

    assert pid._temperature_resolution == pytest.approx(0.1, rel=1e-3)


def test_derivative_decays_on_large_sensor_gap():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, proportional=0.0, integral=0.0, derivative=1.0),
    )

    _set_heating_curve_value(pid, 10.0)
    pid.update(_state_for_error(1.0, 10.0, current=20.0))
    pid.update(_state_for_error(1.0, 20.0, current=21.0))

    previous = pid.raw_derivative
    assert previous != 0.0

    late_state = _state_for_error(1.0, 20.0 + DERIVATIVE_MAX_INTERVAL + 60.0, current=22.0)
    pid.update(late_state)

    expected = round(previous * DERIVATIVE_DECAY, 3)
    assert pid.raw_derivative == pytest.approx(expected, rel=1e-3)


def test_derivative_freeze_when_delta_below_resolution():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, proportional=0.0, integral=0.0, derivative=1.0),
    )

    _set_heating_curve_value(pid, 10.0)
    pid.update(_state_for_error(1.0, 0.0, current=20.0))
    pid.update(_state_for_error(1.0, 10.0, current=20.1))
    pid.update(_state_for_error(1.0, 20.0, current=20.2))

    pid._raw_derivative = 3.0
    pid.update(_state_for_error(1.0, 30.0, current=20.25))

    assert pid.raw_derivative == pytest.approx(3.0, rel=1e-3)


def test_derivative_freeze_when_delta_is_zero():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, proportional=0.0, integral=0.0, derivative=1.0),
    )

    _set_heating_curve_value(pid, 10.0)
    pid.update(_state_for_error(1.0, 0.0, current=20.0))
    pid.update(_state_for_error(1.0, 10.0, current=20.1))
    pid.update(_state_for_error(1.0, 20.0, current=20.2))

    pid._raw_derivative = 3.0
    pid.update(_state_for_error(1.0, 30.0, current=20.2))

    assert pid.raw_derivative == pytest.approx(3.0, rel=1e-3)
