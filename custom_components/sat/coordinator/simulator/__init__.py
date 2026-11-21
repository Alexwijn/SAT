from __future__ import annotations

from typing import Optional

from homeassistant.core import HomeAssistant

from .. import SatDataUpdateCoordinator
from ...const import MINIMUM_SETPOINT
from ...entry_data import SatConfig
from ...helpers import seconds_since, timestamp
from ...types import HeaterState


class SatSimulatorCoordinator(SatDataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config: SatConfig) -> None:
        """Initialize."""
        super().__init__(hass, config)

        self._setpoint = MINIMUM_SETPOINT
        self._boiler_temperature = MINIMUM_SETPOINT

        self._device_state = HeaterState.OFF
        self._device_on_since: Optional[float] = None
        self._heating = self._config.simulation.simulated_heating
        self._cooling = self._config.simulation.simulated_cooling
        self._maximum_setpoint = self._config.limits.maximum_setpoint or MINIMUM_SETPOINT
        self._warming_up = self._config.simulation.simulated_warming_up_seconds

    @property
    def id(self) -> str:
        return 'Simulator'

    @property
    def type(self) -> str:
        return "Simulator"

    @property
    def supports_setpoint_management(self) -> bool:
        return True

    @property
    def supports_maximum_setpoint_management(self):
        return True

    @property
    def supports_relative_modulation(self) -> Optional[float]:
        return True

    @property
    def setpoint(self) -> float:
        return self._setpoint

    @property
    def boiler_temperature(self) -> Optional[float]:
        return self._boiler_temperature

    @property
    def active(self) -> bool:
        return self._device_state == HeaterState.ON

    @property
    def flame_active(self) -> bool:
        return self.active and self.target > self._boiler_temperature

    @property
    def relative_modulation_value(self) -> Optional[float]:
        return 100 if self.flame_active else 0

    @property
    def member_id(self) -> Optional[int]:
        return -1

    async def async_set_heater_state(self, state: HeaterState) -> None:
        self._device_state = state
        if state == HeaterState.ON:
            if self._device_on_since is None:
                self._device_on_since = timestamp()
        else:
            self._device_on_since = None

        await super().async_set_heater_state(state)

    async def async_set_control_setpoint(self, value: float) -> None:
        self._setpoint = value
        await super().async_set_control_setpoint(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        self._maximum_setpoint = value
        await super().async_set_control_max_setpoint(value)

    async def async_control_heating_loop(self, timestamp: float) -> None:
        await super().async_control_heating_loop(timestamp)

        # Calculate the difference, so we know when to slowdown
        difference = abs(self._boiler_temperature - self.target)
        self.logger.debug(f"Target: {self.target}, Current: {self._boiler_temperature}, Difference: {difference}")

        # Heating
        if self.target >= self._boiler_temperature:
            if self._heating >= difference:
                self._boiler_temperature = self.target
                self.logger.debug(f"Reached boiler temperature")
            else:
                self._boiler_temperature += self._heating
                self.logger.debug(f"Increasing boiler temperature with {self._heating}")

        # Cooling
        elif self._boiler_temperature >= self.target:
            if self._cooling >= difference:
                self._boiler_temperature = self.target
                self.logger.debug(f"Reached boiler temperature")
            else:
                self._boiler_temperature -= self._cooling
                self.logger.debug(f"Decreasing boiler temperature with {self._cooling}")

        # Notify listeners to ensure the entities are updated
        self.async_notify_listeners()

    @property
    def target(self):
        # Overshoot
        if self.minimum_setpoint >= self.setpoint:
            return self.minimum_setpoint

        # State check
        if not self._device_on_since or seconds_since(self._device_on_since) < self._warming_up:
            return MINIMUM_SETPOINT

        return self.setpoint
