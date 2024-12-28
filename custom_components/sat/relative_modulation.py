import logging
from enum import Enum

from .const import MINIMUM_SETPOINT
from .coordinator import SatDataUpdateCoordinator
from .pwm import PWMState

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
        self._pwm_state = None
        self._coordinator = coordinator
        self._heating_system = heating_system

        _LOGGER.debug("Relative Modulation initialized for heating system: %s", heating_system)

    async def update(self, state: PWMState) -> None:
        """Update internal state with new data received from the coordinator"""
        self._pwm_state = state

        _LOGGER.debug("Updated Relative Modulation: enabled=%s, state=%s", self.enabled, self.state)

    @property
    def state(self) -> RelativeModulationState:
        """Determine the current state of relative modulation based on coordinator and internal data"""
        # If setpoint is not available or below the minimum threshold, it's considered COLD
        if self._coordinator.setpoint is None or self._coordinator.setpoint <= MINIMUM_SETPOINT:
            return RelativeModulationState.COLD

        # If hot water is actively being used, it's considered HOT_WATER
        if self._coordinator.hot_water_active:
            return RelativeModulationState.HOT_WATER

        # If the PWM state is in the ON state, it's considered PULSE_WIDTH_MODULATION_OFF
        if self._pwm_state != PWMState.ON:
            return RelativeModulationState.PULSE_WIDTH_MODULATION_OFF

        # Default case, when none of the above conditions are met, it's considered OFF
        return RelativeModulationState.OFF

    @property
    def enabled(self) -> bool:
        """Check if the relative modulation is enabled based on its current state"""
        # Relative modulation is considered enabled if it's not in the OFF state
        return self.state != RelativeModulationState.OFF
