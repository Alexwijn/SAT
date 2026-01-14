"""Tests for boiler status evaluation."""

from custom_components.sat.boiler import BoilerState
from custom_components.sat.boiler.status import BoilerStatusEvaluator, BoilerStatusSnapshot
from custom_components.sat.types import BoilerStatus


def _state(*, flow: float, setpoint: float, flame_active: bool = True) -> BoilerState:
    return BoilerState(
        flame_active=flame_active,
        central_heating=True,
        hot_water_active=False,
        modulation_reliable=True,
        flame_on_since=None,
        flame_off_since=None,
        setpoint=setpoint,
        flow_temperature=flow,
        return_temperature=flow - 5.0,
        max_modulation_level=100,
        relative_modulation_level=50.0,
    )


def test_status_ramping_up_detects_fast_rise():
    previous = _state(flow=40.0, setpoint=50.0)
    current = _state(flow=41.0, setpoint=50.0)

    status = BoilerStatusEvaluator.evaluate(BoilerStatusSnapshot(
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

    status = BoilerStatusEvaluator.evaluate(BoilerStatusSnapshot(
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
