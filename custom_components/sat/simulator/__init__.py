from __future__ import annotations

import typing
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .. import CONF_SIMULATED_HEATING, CONF_SIMULATED_COOLING, MINIMUM_SETPOINT, CONF_SIMULATED_WARMING_UP, CONF_MAXIMUM_SETPOINT
from ..coordinator import DeviceState, SatDataUpdateCoordinator
from ..util import convert_time_str_to_seconds

if typing.TYPE_CHECKING:
    from ..climate import SatClimate


class SatSimulatorCoordinator(SatDataUpdateCoordinator):
    """Class to manage the Switch."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(hass, config_entry)

        self._started_on = None
        self._setpoint = MINIMUM_SETPOINT
        self._boiler_temperature = MINIMUM_SETPOINT

        self._heating = config_entry.data.get(CONF_SIMULATED_HEATING)
        self._cooling = config_entry.data.get(CONF_SIMULATED_COOLING)
        self._maximum_setpoint = config_entry.data.get(CONF_MAXIMUM_SETPOINT)
        self._warming_up = convert_time_str_to_seconds(config_entry.data.get(CONF_SIMULATED_WARMING_UP))

    @property
    def supports_setpoint_management(self) -> bool:
        return True

    @property
    def supports_maximum_setpoint_management(self):
        return True

    @property
    def supports_relative_modulation_management(self) -> float | None:
        return True

    @property
    def setpoint(self) -> float:
        return self._setpoint

    @property
    def boiler_temperature(self) -> float | None:
        return self._boiler_temperature

    @property
    def device_active(self) -> bool:
        return self._device_state == DeviceState.ON

    @property
    def flame_active(self) -> bool:
        return self.device_active and self.target > self._boiler_temperature

    @property
    def relative_modulation_value(self) -> float | None:
        return 100 if self.flame_active else 0

    async def async_set_heater_state(self, state: DeviceState) -> None:
        self._started_on = monotonic() if state == DeviceState.ON else None

        await super().async_set_heater_state(state)

    async def async_set_control_setpoint(self, value: float) -> None:
        self._setpoint = value
        await super().async_set_control_setpoint(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        self._maximum_setpoint = value
        await super().async_set_control_max_setpoint(value)

    async def async_control_heating_loop(self, climate: SatClimate = None, _time=None) -> None:
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

        self.async_set_updated_data({})

    @property
    def target(self):
        # Overshoot
        if self.minimum_setpoint >= self.setpoint:
            return self.minimum_setpoint

        # State check
        if not self._started_on or (monotonic() - self._started_on) < self._warming_up:
            return MINIMUM_SETPOINT

        return self.setpoint
