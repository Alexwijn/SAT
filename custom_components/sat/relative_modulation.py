import logging
from typing import Optional

from .const import MINIMUM_SETPOINT
from .coordinator import SatDataUpdateCoordinator
from .pwm import PWMState
from .types import RelativeModulationState, PWMStatus

_LOGGER = logging.getLogger(__name__)


class RelativeModulation:
    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_system: str):
        """Initialize instance variables"""
        self._heating_system: str = heating_system
        self._coordinator: SatDataUpdateCoordinator = coordinator

        self._pwm: Optional[PWMState] = None
        _LOGGER.debug("Relative Modulation initialized for heating system: %s", heating_system)

    async def update(self, pwm: PWMState) -> None:
        """Update internal state with new internal data"""
        self._pwm = pwm

    @property
    def state(self) -> RelativeModulationState:
        """Determine the current state of relative modulation based on coordinator and internal data"""
        if self._coordinator.hot_water_active:
            return RelativeModulationState.HOT_WATER

        if self._coordinator.setpoint is None or self._coordinator.setpoint <= MINIMUM_SETPOINT:
            return RelativeModulationState.COLD

        if self._pwm is None or self._pwm.status == PWMStatus.IDLE:
            return RelativeModulationState.PWM_OFF

        return RelativeModulationState.OFF

    @property
    def enabled(self) -> bool:
        """Check if the relative modulation is enabled based on its current state"""
        return self.state != RelativeModulationState.OFF
