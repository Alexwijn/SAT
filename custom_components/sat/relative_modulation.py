import logging
from enum import Enum

from .const import MINIMUM_SETPOINT
from .coordinator import SatDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


# Enum to represent different states of relative modulation
class RelativeModulationState(str, Enum):
    OFF = "off"
    COLD = "cold"
    HOT_WATER = "hot_water"
    PULSE_WIDTH_MODULATION_OFF = "pulse_width_modulation_off"


class RelativeModulation:
    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_system: str):
        """Initialize instance variables"""
        self._coordinator = coordinator
        self._heating_system = heating_system
        self._pulse_width_modulation_enabled = None

        _LOGGER.debug("Relative Modulation initialized for heating system: %s", heating_system)

    async def update(self, pulse_width_modulation_enabled: bool) -> None:
        """Update internal state with new internal data"""
        self._pulse_width_modulation_enabled = pulse_width_modulation_enabled

    @property
    def state(self) -> RelativeModulationState:
        """Determine the current state of relative modulation based on coordinator and internal data"""
        if self._coordinator.setpoint is None or self._coordinator.setpoint <= MINIMUM_SETPOINT:
            return RelativeModulationState.COLD

        if self._coordinator.hot_water_active:
            return RelativeModulationState.HOT_WATER

        if not self._pulse_width_modulation_enabled:
            return RelativeModulationState.PULSE_WIDTH_MODULATION_OFF

        return RelativeModulationState.OFF

    @property
    def enabled(self) -> bool:
        """Check if the relative modulation is enabled based on its current state"""
        return self.state != RelativeModulationState.OFF
