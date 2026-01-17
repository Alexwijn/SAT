"""Tests for boiler status evaluation."""

from custom_components.sat.device import DeviceState
from custom_components.sat.device.status import DeviceStatusEvaluator, DeviceStatusSnapshot
from custom_components.sat.types import BoilerStatus


def _state(*, flow: float, setpoint: float, flame_active: bool = True) -> DeviceState:
    return DeviceState(
        flame_active=flame_active,
        central_heating=True,
        hot_water_active=False,
        setpoint=setpoint,
        flow_temperature=flow,
        return_temperature=flow - 5.0,
        max_modulation_level=100,
        relative_modulation_level=50.0,
    )


def test_status_ramping_up_detects_fast_rise():
    previous = _state(flow=40.0, setpoint=50.0)
    current = _state(flow=41.0, setpoint=50.0)

    status = DeviceStatusEvaluator.evaluate(DeviceStatusSnapshot(
        last_cycle=None,
        last_flame_on_at=95.0,
        last_flame_off_at=None,
        last_flame_off_was_overshoot=False,
        last_update_at=100.0,
        previous_update_at=98.0,
        modulation_direction=0,
        previous_state=previous,
        state=current,
    ))

    assert status is BoilerStatus.IGNITION_SURGE


def test_status_ramping_up_outside_window_falls_back_to_preheat():
    previous = _state(flow=40.0, setpoint=50.0)
    current = _state(flow=41.0, setpoint=50.0)

    status = DeviceStatusEvaluator.evaluate(DeviceStatusSnapshot(
        last_cycle=None,
        last_flame_on_at=10.0,
        last_flame_off_at=None,
        last_flame_off_was_overshoot=False,
        last_update_at=100.0,
        previous_update_at=98.0,
        modulation_direction=0,
        previous_state=previous,
        state=current,
    ))

    assert status is BoilerStatus.PREHEATING
