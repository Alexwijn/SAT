"""Tests for the PID controller."""

from datetime import datetime

import pytest

from custom_components.sat.const import DEADBAND, HeatingSystem
from custom_components.sat.pid import (
    DERIVATIVE_ALPHA1,
    DERIVATIVE_ALPHA2,
    DERIVATIVE_RAW_CAP,
    PID,
    SENSOR_MAX_INTERVAL,
)
from custom_components.sat.temperature_state import TemperatureState


class TimestampSequence:
    """Return deterministic timestamp values, repeating the last one."""

    def __init__(self, values):
        self._values = iter(values)
        self._last = None

    def __call__(self):
        try:
            self._last = next(self._values)
        except StopIteration:
            if self._last is None:
                raise
        return self._last


def _patch_timestamp(monkeypatch, values):
    timestamp = TimestampSequence(values)
    monkeypatch.setattr("custom_components.sat.pid.timestamp", timestamp)
    return timestamp


def _state_for_error(error, timestamp_value, current=20.0):
    setpoint = current + error
    timestamp_dt = datetime.fromtimestamp(timestamp_value)
    return TemperatureState(
        entity_id="climate.test",
        current=current,
        setpoint=setpoint,
        last_updated=timestamp_dt,
        last_changed=timestamp_dt,
    )


def test_initial_state_and_availability(monkeypatch):
    _patch_timestamp(monkeypatch, [100.0])

    pid = PID(
        heating_system=HeatingSystem.RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=1.0,
        ki=0.5,
        kd=0.1,
    )

    assert pid.available is False
    assert pid.integral == 0.0
    assert pid.raw_derivative == 0.0
    assert pid.derivative == 0.0
    assert pid.output == 0.0


def test_manual_gains_output_and_availability(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0])

    pid = PID(
        heating_system=HeatingSystem.RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=2.0,
        ki=1.0,
        kd=0.5,
        automatic_gains=False,
    )

    state = _state_for_error(0.05, 10.0)
    pid.update(state, heating_curve=30.0)

    assert pid.available is True
    assert pid.kp == 2.0
    assert pid.ki == 1.0
    assert pid.kd == 0.5
    assert pid.proportional == 0.1
    assert pid.integral == 0.5
    assert pid.derivative == 0.0
    assert pid.output == 30.6


def test_automatic_gains_calculation(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0])

    pid = PID(
        heating_system=HeatingSystem.UNDERFLOOR,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=1.0,
        ki=1.0,
        kd=1.0,
        automatic_gains=True,
    )

    assert pid.kp == 0.0

    state = _state_for_error(0.05, 5.0)
    pid.update(state, heating_curve=40.0)

    assert pid.kp == 20.0
    assert pid.ki == round(20.0 / 8400, 6)
    assert pid.kd == round(0.07 * 8400 * 20.0, 6)


def test_integral_timebase_reset_and_accumulation(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0])

    pid = PID(
        heating_system=HeatingSystem.RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=1.0,
        ki=1.0,
        kd=0.0,
    )

    pid.update(_state_for_error(DEADBAND + 0.4, 10.0), heating_curve=10.0)
    assert pid.integral == 0.0

    pid.update(_state_for_error(DEADBAND / 2, 20.0), heating_curve=10.0)
    assert pid.integral == 0.0

    pid.update(_state_for_error(DEADBAND / 2, 30.0), heating_curve=10.0)
    assert pid.integral == 0.5


def test_integral_clamped_to_heating_curve(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0])

    pid = PID(
        heating_system=HeatingSystem.RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=1.0,
        ki=1.0,
        kd=0.0,
    )

    pid.update(_state_for_error(DEADBAND, 10.0), heating_curve=0.5)

    assert pid.integral == 0.5


def test_integral_clamps_large_interval(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0])

    pid = PID(
        heating_system=HeatingSystem.RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=1.0,
        ki=1.0,
        kd=0.0,
    )

    state = _state_for_error(0.05, SENSOR_MAX_INTERVAL + 600.0)
    pid.update(state, heating_curve=100.0)

    expected = 0.05 * SENSOR_MAX_INTERVAL
    assert pid.integral == pytest.approx(expected, rel=1e-3)


def test_derivative_filtering_and_cap(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0])

    pid = PID(
        heating_system=HeatingSystem.RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=0.0,
        ki=0.0,
        kd=1.0,
    )

    pid.update(_state_for_error(1.0, 10.0, current=10.0), heating_curve=10.0)
    pid.update(_state_for_error(1.0, 11.0, current=11.0), heating_curve=10.0)

    derivative = (11.0 - 10.0) / 1.0
    expected_raw = DERIVATIVE_ALPHA2 * (DERIVATIVE_ALPHA1 * derivative)

    assert pid.raw_derivative == pytest.approx(round(expected_raw, 3), rel=1e-3)
    assert pid.derivative == pytest.approx(expected_raw, rel=1e-3)

    pid.update(_state_for_error(1.0, 12.0, current=1000.0), heating_curve=10.0)
    assert pid.raw_derivative == DERIVATIVE_RAW_CAP


def test_derivative_freeze_in_deadband(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0])

    pid = PID(
        heating_system=HeatingSystem.RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=0.0,
        ki=0.0,
        kd=1.0,
    )

    pid.update(_state_for_error(1.0, 10.0, current=10.0), heating_curve=10.0)
    pid._raw_derivative = 3.0

    pid.update(_state_for_error(DEADBAND / 2, 20.0, current=10.0), heating_curve=10.0)

    assert pid.raw_derivative == pytest.approx(3.0, rel=1e-3)


def test_derivative_uses_sensor_timing(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0])

    pid = PID(
        heating_system=HeatingSystem.RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=0.0,
        ki=0.0,
        kd=1.0,
    )

    pid.update(_state_for_error(1.0, 100.0, current=10.0), heating_curve=10.0)
    pid.update(_state_for_error(1.0, 300.0, current=11.0), heating_curve=10.0)

    derivative = (11.0 - 10.0) / 200.0
    expected_raw = DERIVATIVE_ALPHA2 * (DERIVATIVE_ALPHA1 * derivative)

    assert pid.raw_derivative == pytest.approx(round(expected_raw, 3), rel=1e-3)
