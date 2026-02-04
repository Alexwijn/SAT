import logging
from typing import Optional

from . import SatConfig
from .types import HeatingSystem

_LOGGER = logging.getLogger(__name__)


class HeatingCurve:
    """Compute and store heating curve targets based on outdoor temperature."""

    def __init__(self, heating_system: HeatingSystem, coefficient: float):
        self._value: Optional[float] = None
        self._coefficient: float = coefficient
        self._heating_system: HeatingSystem = heating_system

    @property
    def value(self) -> Optional[float]:
        return self._value

    @staticmethod
    def from_config(config: SatConfig):
        """Build a heating curve using values from the integration config."""
        return HeatingCurve(heating_system=config.heating_system, coefficient=config.pid.heating_curve_coefficient)

    @staticmethod
    def calculate(target_temperature: float, outside_temperature: float) -> float:
        """Return the unscaled curve value for the given target and outdoor temperatures."""
        return 4 * (target_temperature - 20) + 0.03 * (outside_temperature - 20) ** 2 - 0.4 * (outside_temperature - 20)

    def reset(self):
        """Clear the cached curve value."""
        self._value = None

    def update(self, target_temperature: float, outside_temperature: float) -> None:
        """Recalculate and store the scaled curve value for the current conditions."""
        heating_curve_value = self.calculate(target_temperature, outside_temperature)
        self._value = round(self._heating_system.base_offset + ((self._coefficient / 4) * heating_curve_value), 1)
