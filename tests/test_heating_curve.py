"""Autotuning cannot infer a coefficient from a zero-valued curve."""
import pytest

from custom_components.sat.const import HEATING_SYSTEM_UNDERFLOOR, HEATING_SYSTEM_RADIATORS
from custom_components.sat.heating_curve import HeatingCurve


@pytest.mark.parametrize("heating_system", [HEATING_SYSTEM_UNDERFLOOR, HEATING_SYSTEM_RADIATORS])
@pytest.mark.parametrize("restored", [False, True])
def test_zero_curve_autotune_preserves_history(heating_system, restored):
    curve = HeatingCurve(heating_system, 2.0)
    control = HeatingCurve(heating_system, 2.0)
    if restored:
        curve.restore_autotune(2.0, 0.4)
        control.restore_autotune(2.0, 0.4)
    before = (curve.optimal_coefficient, curve.coefficient_derivative)

    assert curve.calculate(20, 20) == 0
    curve.autotune(35, 20, 20)
    assert (curve.optimal_coefficient, curve.coefficient_derivative) == before

    curve.autotune(35, 21, 10)
    control.autotune(35, 21, 10)
    assert curve.optimal_coefficient == control.optimal_coefficient
    assert curve.coefficient_derivative == control.coefficient_derivative


@pytest.mark.parametrize("heating_system", [HEATING_SYSTEM_UNDERFLOOR, HEATING_SYSTEM_RADIATORS])
def test_coefficient_is_undefined_for_zero_curve(heating_system):
    curve = HeatingCurve(heating_system, 2.0)
    assert curve.calculate_coefficient(35, 20, 20) is None


@pytest.mark.parametrize("heating_system, expected", [(HEATING_SYSTEM_UNDERFLOOR, 5.5), (HEATING_SYSTEM_RADIATORS, 2.8)])
def test_nonzero_curve_coefficient_is_unchanged(heating_system, expected):
    curve = HeatingCurve(heating_system, 2.0)
    assert curve.calculate_coefficient(35, 21, 10) == expected
