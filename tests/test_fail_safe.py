from custom_components.sat.const import FAIL_SAFE_GRACE_SECONDS
from custom_components.sat.fail_safe import FailSafeMonitor
from custom_components.sat.types import ControlSignal, FailSafePhase


def test_monitor_holds_then_releases():
    monitor = FailSafeMonitor()

    assert monitor.update(ControlSignal.INSIDE_TEMPERATURE, 1000.0).phase is FailSafePhase.HOLDING
    assert monitor.update(ControlSignal.INSIDE_TEMPERATURE, 999.0 + FAIL_SAFE_GRACE_SECONDS).phase is FailSafePhase.HOLDING

    released = monitor.update(ControlSignal.OUTSIDE_TEMPERATURE, 1000.0 + FAIL_SAFE_GRACE_SECONDS)

    assert released.phase is FailSafePhase.RELEASED
    assert released.just_released is True
    assert monitor.update(ControlSignal.OUTSIDE_TEMPERATURE, 1010.0 + FAIL_SAFE_GRACE_SECONDS).just_released is False


def test_monitor_restarts_the_grace_period_after_the_signal_returns():
    monitor = FailSafeMonitor()
    monitor.update(ControlSignal.INSIDE_TEMPERATURE, 1000.0)
    monitor.update(ControlSignal.INSIDE_TEMPERATURE, 1000.0 + FAIL_SAFE_GRACE_SECONDS)

    assert monitor.update(None, 2000.0).just_resumed is True
    assert monitor.update(ControlSignal.INSIDE_TEMPERATURE, 2010.0).phase is FailSafePhase.HOLDING
