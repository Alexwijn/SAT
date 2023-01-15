from collections import deque
from statistics import mean

from custom_components.sat import *


class HeatingCurve:
    def __init__(self, heating_system: str, coefficient: float, comfort_temp: float):
        """
        :param heating_system: The type of heating system, either "underfloor" or "radiator"
        :param coefficient: The coefficient used to adjust the heating curve
        :param comfort_temp: The user comfort temperature of the living room
        """
        self._coefficient = coefficient
        self._comfort_temp = comfort_temp
        self._heating_system = heating_system
        self.reset()

    def reset(self):
        self._optimal_coefficient = None
        self._optimal_coefficients = deque(maxlen=5)

    def calculate_value(self, outside_temperature: float) -> float:
        """Calculate the heating curve based on the outside temperature."""
        base_offset = self._get_base_offset()
        heating_curve_value = self._get_heating_curve_value(outside_temperature)

        return round(base_offset + ((self._coefficient / 4) * heating_curve_value), 1)

    def calculate_coefficient(self, setpoint: float, outside_temperature: float) -> float:
        """Convert a setpoint to a coefficient value"""
        base_offset = self._get_base_offset()
        heating_curve_value = self._get_heating_curve_value(outside_temperature)

        return round(4 * (setpoint - base_offset) / heating_curve_value, 1)

    def autotune(self, setpoints: deque, outside_temperature: float):
        coefficient = self.calculate_coefficient(
            setpoint=sum(setpoints) / len(setpoints),
            outside_temperature=outside_temperature
        )

        if coefficient != self._optimal_coefficient:
            self._optimal_coefficients.append(coefficient)

        self._optimal_coefficient = coefficient

    def _get_base_offset(self) -> float:
        """Determine the base offset for the heating system."""
        return 20 if self._heating_system == HEATING_SYSTEM_UNDERFLOOR else 28

    def _get_heating_curve_value(self, current_outside_temperature: float) -> float:
        """Calculate the heating curve value based on the current outside temperature"""
        return self._comfort_temp - (0.01 * current_outside_temperature ** 2) - (0.8 * current_outside_temperature)

    @property
    def optimal_coefficient(self):
        return sum(mean(self._optimal_coefficients), 1)
