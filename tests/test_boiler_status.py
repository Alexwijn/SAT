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
        last_hot_water_on_at=None,
        last_hot_water_off_at=None,
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
        last_hot_water_on_at=None,
        last_hot_water_off_at=None,
        last_update_at=100.0,
        previous_update_at=98.0,
        modulation_direction=0,
        previous_state=previous,
        state=current,
    ))

    assert status is BoilerStatus.PREHEATING


def _snapshot(state: DeviceState, previous: DeviceState | None) -> DeviceStatusSnapshot:
    return DeviceStatusSnapshot(
        last_cycle=None,
        last_flame_on_at=None,
        last_flame_off_at=None,
        last_flame_off_was_overshoot=False,
        last_hot_water_on_at=None,
        last_hot_water_off_at=None,
        last_update_at=100.0,
        previous_update_at=98.0,
        modulation_direction=0,
        previous_state=previous,
        state=state,
    )


def test_status_hot_water_has_priority_over_suspended_central_heating():
    heating_hot_water = DeviceState(
        flame_active=True, hot_water_active=True, central_heating=False,
        setpoint=60.0, flow_temperature=55.0, return_temperature=40.0, max_modulation_level=100, relative_modulation_level=80.0,
    )

    assert DeviceStatusEvaluator.evaluate(_snapshot(heating_hot_water, None)) is BoilerStatus.HEATING_HOT_WATER

    flame_off = DeviceState(
        flame_active=False, hot_water_active=True, central_heating=False,
        setpoint=60.0, flow_temperature=55.0, return_temperature=40.0, max_modulation_level=100, relative_modulation_level=0.0,
    )

    assert DeviceStatusEvaluator.evaluate(_snapshot(flame_off, heating_hot_water)) is BoilerStatus.COOLING
    assert DeviceStatusEvaluator.evaluate(_snapshot(flame_off, None)) is BoilerStatus.IDLE


def test_status_fresh_demand_after_long_off_phase_is_not_stalled():
    previous = _state(flow=30.0, setpoint=10.0, flame_active=False)
    current = _state(flow=30.0, setpoint=52.0, flame_active=False)

    status = DeviceStatusEvaluator.evaluate(DeviceStatusSnapshot(
        last_cycle=None,
        last_flame_on_at=100.0,
        last_flame_off_at=200.0,
        last_flame_off_was_overshoot=False,
        last_hot_water_on_at=None,
        last_hot_water_off_at=None,
        last_update_at=1700.0,
        previous_update_at=1690.0,
        modulation_direction=0,
        previous_state=previous,
        state=current,
        last_demand_started_at=1700.0,
    ))

    assert status is BoilerStatus.WAITING_FOR_FLAME


def test_status_demand_unanswered_for_too_long_is_stalled():
    previous = _state(flow=30.0, setpoint=52.0, flame_active=False)
    current = _state(flow=30.0, setpoint=52.0, flame_active=False)

    status = DeviceStatusEvaluator.evaluate(DeviceStatusSnapshot(
        last_cycle=None,
        last_flame_on_at=100.0,
        last_flame_off_at=200.0,
        last_flame_off_was_overshoot=False,
        last_hot_water_on_at=None,
        last_hot_water_off_at=None,
        last_update_at=2400.0,
        previous_update_at=2390.0,
        modulation_direction=0,
        previous_state=previous,
        state=current,
        last_demand_started_at=1700.0,
    ))

    assert status is BoilerStatus.STALLED_IGNITION

