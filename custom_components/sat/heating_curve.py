import logging
from collections import deque
from statistics import mean

from .const import *

_LOGGER = logging.getLogger(__name__)


class HeatingCurve:
    def __init__(self, heating_system: str, coefficient: float, version: int = 3):
        """
        :param heating_system: The type of heating system, either "underfloor" or "radiator"
        :param coefficient: The coefficient used to adjust the heating curve
        :param version: The version of math calculation for the heating curve
        """
        self._version = version
        self._coefficient = coefficient
        self._heating_system = heating_system
        self.reset()

    def reset(self):
        self._optimal_coefficient = None
        self._coefficient_derivative = None
        self._last_heating_curve_value = None
        self._optimal_coefficients = deque(maxlen=5)

    def update(self, target_temperature: float, outside_temperature: float) -> None:
        """Calculate the heating curve based on the outside temperature."""
        heating_curve_value = self._get_heating_curve_value(target_temperature, outside_temperature)
        self._last_heating_curve_value = round(self.base_offset + ((self._coefficient / 4) * heating_curve_value), 1)

    def calculate_coefficient(self, setpoint: float, target_temperature: float, outside_temperature: float) -> float:
        """Convert a setpoint to a coefficient value"""
        heating_curve_value = self._get_heating_curve_value(target_temperature, outside_temperature)
        return round(4 * (setpoint - self.base_offset) / heating_curve_value, 1)

    def autotune(self, setpoint: float, target_temperature: float, outside_temperature: float):
        """Calculate an optimal coefficient value."""
        if setpoint <= MINIMUM_SETPOINT:
            return

        coefficient = self.calculate_coefficient(setpoint, target_temperature, outside_temperature)
        self._coefficient_derivative = round(coefficient - self._optimal_coefficient, 1) if self._optimal_coefficient else coefficient

        # Fuzzy logic for when the derivative is positive
        if self._coefficient_derivative > 1:
            coefficient -= 0.3
        elif self._coefficient_derivative < 0.5:
            coefficient -= 0.1
        elif self._coefficient_derivative < 1:
            coefficient -= 0.2

        # Fuzzy logic for when the derivative is negative
        if self._coefficient_derivative < -1:
            coefficient += 0.3
        elif self._coefficient_derivative > -0.5:
            coefficient += 0.1
        elif self._coefficient_derivative > -1:
            coefficient += 0.2

        # Store the results
        self._optimal_coefficients.append(coefficient)
        self._optimal_coefficient = round(mean(self._optimal_coefficients), 1)

    def restore_autotune(self, coefficient: float, derivative: float):
        """Restore a previous optimal coefficient value."""
        self._optimal_coefficient = coefficient
        self._coefficient_derivative = derivative

        self._optimal_coefficients = deque([coefficient] * 5, maxlen=5)

    def _get_heating_curve_value(self, target_temperature: float, outside_temperature: float) -> float:
        """Calculate the heating curve value based on the current outside temperature"""
        if self._version == 1:
            return target_temperature - (0.01 * outside_temperature ** 2) - (0.8 * outside_temperature)

        if self._version == 2:
            return 2.72 * (target_temperature - 20) + 0.03 * (outside_temperature - 20) ** 2 - 1.2 * (outside_temperature - 20)

        if self._version == 3:
            return 4 * (target_temperature - 20) + 0.03 * (outside_temperature - 20) ** 2 - 0.4 * (outside_temperature - 20)

        raise Exception("Invalid version")

    @property
    def base_offset(self) -> float:
        """Determine the base offset for the heating system."""
        return 20 if self._heating_system == HEATING_SYSTEM_UNDERFLOOR else 27.2

    @property
    def optimal_coefficient(self):
        return self._optimal_coefficient

    @property
    def coefficient_derivative(self):
        return self._coefficient_derivative

    @property
    def value(self):
        return self._last_heating_curve_value
