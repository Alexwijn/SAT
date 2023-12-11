from enum import Enum

from custom_components.sat import MINIMUM_SETPOINT, HEATING_SYSTEM_HEAT_PUMP
from custom_components.sat.coordinator import SatDataUpdateCoordinator
from custom_components.sat.pwm import PWMState


# Enum to represent different states of relative modulation
class RelativeModulationState(str, Enum):
    OFF = "off"
    COLD = "cold"
    HOT_WATER = "hot_water"
    WARMING_UP = "warming_up"
    PULSE_WIDTH_MODULATION_OFF = "pulse_width_modulation_off"


class RelativeModulation:
    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_system: str):
        """Initialize instance variables"""
        self._heating_system = heating_system  # The heating system that is being controlled
        self._pwm_state = None  # Tracks the current state of the PWM (Pulse Width Modulation) system
        self._warming_up = False  # Stores data related to the warming up state of the heating system
        self._coordinator = coordinator  # Reference to the data coordinator responsible for system-wide information

    async def update(self, warming_up: bool, state: PWMState) -> None:
        """Update internal state with new data received from the coordinator"""
        self._pwm_state = state
        self._warming_up = warming_up

    @property
    def state(self) -> RelativeModulationState:
        """Determine the current state of relative modulation based on coordinator and internal data"""
        # If setpoint is not available or below the minimum threshold, it's considered COLD
        if self._coordinator.setpoint is None or self._coordinator.setpoint <= MINIMUM_SETPOINT:
            return RelativeModulationState.COLD

        # If hot water is actively being used, it's considered HOT_WATER
        if self._coordinator.hot_water_active:
            return RelativeModulationState.HOT_WATER

        # If the heating system is currently in the process of warming up, it's considered WARMING_UP
        if self._warming_up and self._heating_system != HEATING_SYSTEM_HEAT_PUMP:
            return RelativeModulationState.WARMING_UP

        # If the PWM state is in the ON state, it's considered PULSE_WIDTH_MODULATION_OFF
        if self._pwm_state != PWMState.ON:
            return RelativeModulationState.PULSE_WIDTH_MODULATION_OFF

        # Default case, when none of the above conditions are met, it's considered OFF
        return RelativeModulationState.OFF

    @property
    def enabled(self) -> bool:
        """Check if the relative modulation is enabled based on its current state"""
        # Relative modulation is considered enabled if it's not in the OFF state or in the WARMING_UP state
        return self.state != RelativeModulationState.OFF and self.state != RelativeModulationState.WARMING_UP
