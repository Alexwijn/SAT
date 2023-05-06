from __future__ import annotations

import logging
import typing
from enum import Enum

from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .config_store import SatConfigStore
from .const import *

if typing.TYPE_CHECKING:
    from .climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


class DeviceState(Enum):
    ON = "on"
    OFF = "off"


class SatDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, store: SatConfigStore) -> None:
        """Initialize."""
        self._store = store
        self._device_state = DeviceState.OFF
        self._simulation = bool(self._store.options.get(CONF_SIMULATION))
        self._heating_system = str(self._store.options.get(CONF_HEATING_SYSTEM))

        super().__init__(hass, _LOGGER, name=DOMAIN)

    @property
    def store(self):
        """Return the configuration store for the integration."""
        return self._store

    @property
    def device_state(self):
        """Return the current state of the device."""
        return self._device_state

    @property
    def maximum_setpoint(self) -> float:
        """Return the maximum setpoint temperature that the device can support."""
        if self._heating_system == HEATING_SYSTEM_RADIATOR_HIGH_TEMPERATURES:
            return 75.0

        if self._heating_system == HEATING_SYSTEM_RADIATOR_MEDIUM_TEMPERATURES:
            return 65.0

        if self._heating_system == HEATING_SYSTEM_RADIATOR_LOW_TEMPERATURES:
            return 55.0

        if self._heating_system == HEATING_SYSTEM_UNDERFLOOR:
            return 50.0

    @property
    def supports_setpoint_management(self):
        """Returns whether the device supports setting a setpoint.

        This property is used to determine whether the coordinator can send a setpoint to the device.
        If a device doesn't support setpoint management, the coordinator won't be able to control
        the temperature or other properties of the device.

        Returns:
            A boolean indicating whether the device supports setpoint management. True indicates
            that the device supports it, while False indicates that it does not.
        """
        return False

    async def async_added_to_hass(self, climate: SatClimate) -> None:
        """Perform setup when the integration is added to Home Assistant."""
        pass

    async def async_control_heating_loop(self, climate: SatClimate, _time=None) -> None:
        """Control the heating loop for the device."""
        pass

    async def async_control_setpoint(self, value: float) -> None:
        """Control the setpoint temperature for the device."""
        if self.supports_setpoint_management:
            self.logger.info("Set control setpoint to %d", value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        """Set the state of the device heater."""
        self._device_state = state
        self.logger.info("Set central heater state %s", state)

    async def async_send_notification(self, title: str, message: str, service: str = SERVICE_PERSISTENT_NOTIFICATION):
        """Send a notification to the user."""
        data = {"title": title, "message": message}
        await self.hass.services.async_call(NOTIFY_DOMAIN, service, data)
