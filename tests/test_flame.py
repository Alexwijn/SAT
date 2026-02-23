"""Tests for the flame tracking module."""

from custom_components.sat.boiler import BoilerState
from custom_components.sat.const import BoilerStatus, FlameStatus
from custom_components.sat.flame import Flame


def _make_boiler_state(
    is_active: bool = False,
    flame_active: bool = False,
    status: BoilerStatus = BoilerStatus.IDLE,
    hot_water_active: bool = False,
) -> BoilerState:
    return BoilerState(
        is_active=is_active,
        is_inactive=not is_active,
        status=status,
        flame_active=flame_active,
        hot_water_active=hot_water_active,
        setpoint=None,
        flow_temperature=None,
        return_temperature=None,
        relative_modulation_level=None,
    )


def test_idle_device_active_flame_off_is_not_stuck_off() -> None:
    """Device is active (pump running) but status is IDLE and flame is off.

    This is the post-circulation scenario: ch_enable=true keeps the pump
    running while the setpoint is at minimum so the burner does not fire.
    The flame tracker should report IDLE_OK, not STUCK_OFF.
    """
    flame = Flame()

    # Feed enough ON->OFF cycles so we pass the MIN_ON_SAMPLES_FOR_HEALTH threshold
    for _ in range(4):
        flame.update(_make_boiler_state(is_active=True, flame_active=True, status=BoilerStatus.HEATING_UP))
        flame.update(_make_boiler_state(is_active=True, flame_active=False, status=BoilerStatus.IDLE))

    # Now simulate post-circulation: device active, flame off, status IDLE
    state = _make_boiler_state(is_active=True, flame_active=False, status=BoilerStatus.IDLE)

    # Pretend the flame has been off for longer than STUCK_OFF_SECONDS
    flame.update(state)
    flame._flame_off_monotonic = flame._flame_off_monotonic - 600  # 10 minutes ago

    flame._recompute_health(flame._last_update_monotonic)

    assert flame.health_status == FlameStatus.IDLE_OK
