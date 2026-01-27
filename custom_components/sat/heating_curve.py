import logging
from typing import Optional

from . import SatConfig
from .types import HeatingSystem

_LOGGER = logging.getLogger(__name__)


class HeatingCurve:
    def __init__(self, heating_system: HeatingSystem, coefficient: float):
        self._heating_system: HeatingSystem = heating_system
        self._coefficient: float = coefficient
        self._value: Optional[float] = None
        self._optimal_coefficient: Optional[float] = None

    @property
    def value(self) -> Optional[float]:
        return self._value

    @staticmethod
    def from_config(config: SatConfig):
        """Create an instance from configuration"""
        return HeatingCurve(heating_system=config.heating_system, coefficient=config.pid.heating_curve_coefficient)

    @staticmethod
    def calculate(target_temperature: float, outside_temperature: float) -> float:
        """Calculate the heating curve value based on the current outside temperature"""
        return 4 * (target_temperature - 20) + 0.03 * (outside_temperature - 20) ** 2 - 0.4 * (outside_temperature - 20)

    def reset(self):
        """Reset the heating curve to a clean state."""
        self._value = None
        self._optimal_coefficient = None
        self._coefficient_derivative = None

    def update(self, target_temperature: float, outside_temperature: float) -> None:
        """Calculate the heating curve based on the outside temperature."""
        heating_curve_value = self.calculate(target_temperature, outside_temperature)
        self._value = round(self._heating_system.base_offset + ((self._coefficient / 4) * heating_curve_value), 1)

    def calculate_coefficient(self, setpoint: float, target_temperature: float, outside_temperature: float) -> float:
        """Convert a setpoint to a coefficient value"""
        heating_curve_value = self.calculate(target_temperature, outside_temperature)
        return round(4 * (setpoint - self._heating_system.base_offset) / heating_curve_value, 1)
