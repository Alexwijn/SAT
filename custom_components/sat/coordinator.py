from __future__ import annotations

import logging
import typing
from abc import abstractmethod
from enum import Enum
from functools import partial

from homeassistant.components.climate import HVACMode
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .config_store import SatConfigStore
from .const import *
from .services import start_overshoot_protection_calculation, set_overshoot_protection_value

if typing.TYPE_CHECKING:
    from .climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


class DeviceState(Enum):
    ON = "on"
    OFF = "off"


class SatDataUpdateCoordinatorFactory:
    @staticmethod
    async def resolve(hass: HomeAssistant, store: SatConfigStore, mode: str, device: str) -> DataUpdateCoordinator:
        if mode == MODE_MQTT:
            from .mqtt import SatMqttCoordinator
            return SatMqttCoordinator(hass, store, device)

        if mode == MODE_SERIAL:
            from .serial import SatSerialCoordinator
            return await SatSerialCoordinator(hass, store, device).async_connect()

        if mode == MODE_SWITCH:
            from .switch import SatSwitchCoordinator
            return SatSwitchCoordinator(hass, store, device)

        raise Exception(f'Invalid mode[{mode}]')


class SatDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, store: SatConfigStore) -> None:
        """Initialize."""
        self._store = store
        self._device_state = DeviceState.OFF
        self._simulation = bool(self._store.options.get(CONF_SIMULATION))
        self._minimum_setpoint = float(self._store.options.get(CONF_SETPOINT))
        self._heating_system = str(self._store.options.get(CONF_HEATING_SYSTEM))
        self._overshoot_protection = bool(self._store.options.get(CONF_OVERSHOOT_PROTECTION))
        self._force_pulse_width_modulation = bool(self._store.options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))

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
    @abstractmethod
    def setpoint(self) -> float | None:
        pass

    @property
    @abstractmethod
    def device_active(self) -> bool:
        pass

    @property
    def flame_active(self) -> bool:
        return self.device_active

    @property
    def hot_water_active(self) -> bool:
        return False

    @property
    def hot_water_setpoint(self) -> float | None:
        return None

    @property
    def boiler_temperature(self) -> float | None:
        return None

    @property
    def minimum_hot_water_setpoint(self) -> float:
        return 30

    @property
    def maximum_hot_water_setpoint(self) -> float:
        return 60

    @property
    def relative_modulation_value(self) -> float | None:
        return None

    @property
    def boiler_capacity(self) -> float | None:
        return None

    @property
    def minimum_relative_modulation_value(self) -> float | None:
        return None

    @property
    def minimum_setpoint(self) -> float:
        return self._minimum_setpoint

    @property
    def supports_setpoint_management(self):
        """Returns whether the device supports setting a boiler setpoint.

        This property is used to determine whether the coordinator can send a setpoint to the device.
        If a device doesn't support setpoint management, the coordinator won't be able to control the temperature.
        """
        return False

    @property
    def supports_hot_water_setpoint_management(self):
        """Returns whether the device supports setting a hot water setpoint.

        This property is used to determine whether the coordinator can send a setpoint to the device.
        If a device doesn't support setpoint management, the coordinator won't be able to control the temperature.
        """
        return False

    @property
    def support_relative_modulation_management(self):
        """Returns whether the device supports setting a relative modulation value.

        This property is used to determine whether the coordinator can send a relative modulation value to the device.
        If a device doesn't support relative modulation management, the coordinator won't be able to control the value.
        """
        return False

    @property
    def supports_maximum_setpoint_management(self):
        """Returns whether the device supports setting a maximum setpoint.

        This property is used to determine whether the coordinator can send a maximum setpoint to the device.
        If a device doesn't support maximum setpoint management, the coordinator won't be able to control the value.
        """
        return False

    async def async_added_to_hass(self, climate: SatClimate) -> None:
        """Perform setup when the integration is added to Home Assistant."""
        await self.async_set_control_max_setpoint(self.maximum_setpoint)

        if self.supports_setpoint_management:
            if self._overshoot_protection and self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE) is None:
                self._overshoot_protection = False

                await self.async_send_notification(
                    title="Smart Autotune Thermostat",
                    message="Disabled overshoot protection because no overshoot value has been found."
                )

            if self._force_pulse_width_modulation and self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE) is None:
                self._force_pulse_width_modulation = False

                await self.async_send_notification(
                    title="Smart Autotune Thermostat",
                    message="Disabled forced pulse width modulation because no overshoot value has been found."
                )

            self.hass.services.async_register(
                DOMAIN,
                SERVICE_START_OVERSHOOT_PROTECTION_CALCULATION,
                partial(start_overshoot_protection_calculation, self, climate)
            )

            self.hass.services.async_register(
                DOMAIN,
                SERVICE_SET_OVERSHOOT_PROTECTION_VALUE,
                partial(set_overshoot_protection_value, self)
            )

    async def async_will_remove_from_hass(self, climate: SatClimate) -> None:
        """Run when entity will be removed from hass."""
        pass

    async def async_control_heating_loop(self, climate: SatClimate, _time=None) -> None:
        """Control the heating loop for the device."""
        if climate.hvac_mode == HVACMode.OFF and self.device_active:
            # Send out a new command to turn off the device
            await self.async_set_heater_state(DeviceState.OFF)

        if self.support_relative_modulation_management:
            # Determine whether to enable maximum relative modulation value based on the conditions
            relative_modulation_enabled = (
                    # If the climate control is not in heating mode or hot water is requested
                    climate.hvac_mode != HVACMode.HEAT or self.hot_water_active

                    # If the setpoint is below the minimum allowed value (or basically off)
                    or (self.setpoint is not None and self.setpoint <= MINIMUM_SETPOINT)

                    # If the system is not in pulse width modulation mode
                    or not climate.pulse_width_modulation_enabled
            )

            # Control the relative modulation value based on the conditions
            await self.async_set_control_max_relative_modulation(
                MAXIMUM_RELATIVE_MOD if relative_modulation_enabled else MINIMUM_RELATIVE_MOD
            )

    async def async_set_heater_state(self, state: DeviceState) -> None:
        """Set the state of the device heater."""
        self._device_state = state
        self.logger.info("Set central heater state %s", state)

    async def async_set_control_setpoint(self, value: float) -> None:
        """Control the boiler setpoint temperature for the device."""
        if self.supports_setpoint_management:
            self.logger.info("Set control boiler setpoint to %d", value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        """Control the DHW setpoint temperature for the device."""
        if self.supports_hot_water_setpoint_management:
            self.logger.info("Set control hot water setpoint to %d", value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        """Control the setpoint temperature for the thermostat."""
        pass

    async def async_set_control_max_setpoint(self, value: float) -> None:
        """Control the maximum setpoint temperature for the device."""
        if self.supports_maximum_setpoint_management:
            self.logger.info("Set maximum setpoint to %d", value)

    async def async_set_control_max_relative_modulation(self, value: float) -> None:
        """Control the maximum relative modulation for the device."""
        if self.support_relative_modulation_management:
            self.logger.info("Set maximum relative modulation to %d", value)

    async def async_send_notification(self, title: str, message: str, service: str = SERVICE_PERSISTENT_NOTIFICATION):
        """Send a notification to the user."""
        data = {"title": title, "message": message}
        await self.hass.services.async_call(NOTIFY_DOMAIN, service, data)
