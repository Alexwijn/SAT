"""Tests for the PID controller."""

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.sat.const import DEADBAND
from custom_components.sat.entry_data import PidConfig
from custom_components.sat.heating_curve import HeatingCurve
from custom_components.sat.pid import (
    DERIVATIVE_RAW_CAP,
    DERIVATIVE_WINDOW_MAXIMUM_SECONDS,
    DERIVATIVE_WINDOW_MINIMUM_SECONDS,
    PID, PID_UPDATE_INTERVAL,
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
        heating_curve_coefficient: float = 2.0,
) -> PidConfig:
    return PidConfig(
        integral=integral,
        derivative=derivative,
        proportional=proportional,
        automatic_gains=automatic_gains,
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
    assert pid.integral == 6.0
    assert pid.derivative == 0.0
    assert pid.output == 36.1


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


def test_integral_accumulates_inside_deadband_and_holds_outside():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, integral=1.0, derivative=0.0),
    )

    _set_heating_curve_value(pid, 10.0)

    pid.update(_state_for_error(DEADBAND + 0.4, 10.0))
    assert pid.integral == 0.0

    pid.update(_state_for_error(DEADBAND / 2, 20.0))
    assert pid.integral == 3.0

    pid.update(_state_for_error(DEADBAND / 2, 30.0))
    assert pid.integral == 6.0

    pid.update(_state_for_error(DEADBAND + 0.4, 40.0))
    assert pid.integral == 6.0

    pid.update(_state_for_error(DEADBAND / 2, 50.0))
    assert pid.integral == 9.0


def test_integral_clamped_to_heating_curve():
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, integral=1.0, derivative=0.0),
    )

    _set_heating_curve_value(pid, 0.5)
    pid.update(_state_for_error(DEADBAND, 0.0))
    pid.update(_state_for_error(DEADBAND, 10.0))

    assert pid.integral == 0.5


def _derivative_pid() -> PID:
    pid = _make_pid(
        HeatingSystem.RADIATORS,
        config=_pid_config(automatic_gains=False, proportional=0.0, integral=0.0, derivative=6000.0),
    )
    _set_heating_curve_value(pid, 10.0)
    return pid


def _feed_ramp(pid: PID, *, rate_per_hour: float, step: float, seconds: float, interval: float = 60.0, start: float = 20.0) -> None:
    """Feed a steady temperature ramp as a sensor with the given resolution would report it."""
    reported = None
    for elapsed in range(0, int(seconds) + 1, int(interval)):
        true_temperature = start + rate_per_hour * elapsed / 3600.0
        quantized = round(round(true_temperature / step) * step, 3)
        if quantized != reported:
            reported = quantized
            pid.update(_state_for_error(1.0, 1000.0 + elapsed, current=quantized))


def test_derivative_is_slope_over_the_window():
    pid = _derivative_pid()

    for index in range(10):
        pid.update(_state_for_error(1.0, 1000.0 + index * 60.0, current=20.0 + index * 0.01))

    assert pid.raw_derivative == pytest.approx(-0.01 / 60.0, abs=1e-6)
    assert pid.derivative == pytest.approx(-1.0, abs=1e-3)


def test_derivative_holds_until_window_has_enough_span():
    pid = _derivative_pid()
    pid._raw_derivative = 3.0

    pid.update(_state_for_error(1.0, 1000.0, current=20.0))
    pid.update(_state_for_error(1.0, 1060.0, current=20.1))

    assert pid.raw_derivative == pytest.approx(3.0)

    pid.update(_state_for_error(1.0, 1120.0, current=20.2))

    assert pid.raw_derivative == pytest.approx(3.0)

    for index in range(3, 12):
        pid.update(_state_for_error(1.0, 1000.0 + index * 60.0, current=20.0 + index * 0.1))

    assert pid.raw_derivative == pytest.approx(-0.1 / 60.0, abs=1e-6)


def test_derivative_is_capped():
    pid = _derivative_pid()

    for index in range(20):
        pid.update(_state_for_error(1.0, 1000.0 + index * 60.0, current=20.0 + index * 1000.0))

    assert pid.raw_derivative == pytest.approx(-DERIVATIVE_RAW_CAP, abs=1e-6)


def test_implausibly_fast_warming_is_capped():
    pid = _derivative_pid()

    _feed_ramp(pid, rate_per_hour=30.0, step=0.1, seconds=1800)

    assert pid.raw_derivative == pytest.approx(-10.0 / 3600.0, abs=1e-6)


def test_derivative_keeps_working_inside_deadband():
    pid = _derivative_pid()

    for index in range(10):
        pid.update(_state_for_error(DEADBAND / 2, 1000.0 + index * 60.0, current=20.0 + index * 0.01))

    assert pid.raw_derivative == pytest.approx(-0.01 / 60.0, abs=1e-6)


def test_repeated_reports_are_recorded_once():
    pid = _derivative_pid()

    pid.update(_state_for_error(1.0, 1000.0, current=20.0))
    pid.update(_state_for_error(1.0, 1000.0, current=20.0))
    pid.update(_state_for_error(1.0, 1060.0, current=20.1))

    assert len(pid._samples) == 2


def test_samples_outside_the_longest_window_are_dropped():
    pid = _derivative_pid()

    for index in range(40):
        pid.update(_state_for_error(1.0, 1000.0 + index * 60.0, current=20.0 + index * 0.01))

    assert pid._samples[-1].timestamp - pid._samples[0].timestamp <= DERIVATIVE_WINDOW_MAXIMUM_SECONDS


@pytest.mark.parametrize(
    "step, expected_window",
    [
        (0.01, DERIVATIVE_WINDOW_MINIMUM_SECONDS),
        (0.05, DERIVATIVE_WINDOW_MINIMUM_SECONDS),
        (0.1, 1200),
        (0.5, DERIVATIVE_WINDOW_MAXIMUM_SECONDS),
    ],
)
def test_sensor_step_sets_the_derivative_window(step, expected_window):
    pid = _derivative_pid()

    assert pid.derivative_window_seconds == 1200

    _feed_ramp(pid, rate_per_hour=0.6, step=step, seconds=7200)

    assert pid.sensor_step == pytest.approx(step)
    assert pid.derivative_window_seconds == expected_window


@pytest.mark.parametrize("step", [0.01, 0.05, 0.1])
def test_coarse_and_fine_sensors_measure_the_same_ramp(step):
    pid = _derivative_pid()

    _feed_ramp(pid, rate_per_hour=0.6, step=step, seconds=3600)

    expected = -0.6 / 3600.0
    assert pid.raw_derivative == pytest.approx(expected, abs=0.35 * abs(expected))


def test_one_coarse_sensor_step_barely_moves_the_derivative():
    pid = _derivative_pid()

    _feed_ramp(pid, rate_per_hour=0.6, step=0.1, seconds=3600)
    before = pid.raw_derivative

    pid.update(_state_for_error(1.0, 1000.0 + 3660.0, current=pid._samples[-1].temperature + 0.1))

    assert abs(pid.raw_derivative - before) < 0.1 / pid.derivative_window_seconds


async def test_invalid_saved_values_are_ignored(monkeypatch):
    store = Mock(async_load=AsyncMock(return_value={"integral": None, "raw_derivative": "invalid", "last_error": 0.4}))
    monkeypatch.setattr("custom_components.sat.pid.Store", lambda *args, **kwargs: store)
    pid = _derivative_pid()

    await pid.async_added_to_hass(Mock(), "climate.test", "device")

    assert pid.integral == 0.0
    assert pid.raw_derivative == 0.0
    assert pid.last_error == 0.4


async def test_saved_derivative_is_capped(monkeypatch):
    store = Mock(async_load=AsyncMock(return_value={"raw_derivative": 1.0}))
    monkeypatch.setattr("custom_components.sat.pid.Store", lambda *args, **kwargs: store)
    pid = _derivative_pid()

    await pid.async_added_to_hass(Mock(), "climate.test", "device")

    assert pid.raw_derivative == pytest.approx(DERIVATIVE_RAW_CAP, abs=1e-6)


def test_forgetting_the_samples_restarts_the_derivative_window():
    pid = _derivative_pid()
    _feed_ramp(pid, rate_per_hour=0.6, step=0.1, seconds=3600)

    pid.forget_samples()

    assert len(pid._samples) == 0
    assert pid.sensor_step == 0.1
    assert pid._sensor_step is None
