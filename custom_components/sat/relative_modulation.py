import logging

from .const import MINIMUM_SETPOINT
from .coordinator import SatDataUpdateCoordinator
from .types import RelativeModulationState

_LOGGER = logging.getLogger(__name__)


class RelativeModulation:
    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_system: str):
        """Initialize instance variables"""
        self._heating_system: str = heating_system
        self._pulse_width_modulation_enabled: bool = False
        self._coordinator: SatDataUpdateCoordinator = coordinator

        _LOGGER.debug("Relative Modulation initialized for heating system: %s", heating_system)

    async def update(self, pulse_width_modulation_enabled: bool) -> None:
        """Update internal state with new internal data"""
        self._pulse_width_modulation_enabled = pulse_width_modulation_enabled

    @property
    def state(self) -> RelativeModulationState:
        """Determine the current state of relative modulation based on coordinator and internal data"""
        if self._coordinator.hot_water_active:
            return RelativeModulationState.HOT_WATER

        if self._coordinator.setpoint is None or self._coordinator.setpoint <= MINIMUM_SETPOINT:
            return RelativeModulationState.COLD

        if self._pulse_width_modulation_enabled:
            return RelativeModulationState.OFF

        return RelativeModulationState.PWM_OFF

    @property
    def enabled(self) -> bool:
        """Check if the relative modulation is enabled based on its current state"""
        return self.state != RelativeModulationState.OFF
