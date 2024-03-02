from __future__ import annotations

import logging
from abc import abstractmethod
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Mapping, Any

from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import *
from .manufacturer import ManufacturerFactory, Manufacturer
from .util import calculate_default_maximum_setpoint

if TYPE_CHECKING:
    from .climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


class DeviceState(str, Enum):
    ON = "on"
    OFF = "off"


class SatDataUpdateCoordinatorFactory:
    @staticmethod
    async def resolve(
            hass: HomeAssistant,
            mode: str,
            device: str,
            data: Mapping[str, Any],
            options: Mapping[str, Any] | None = None
    ) -> SatDataUpdateCoordinator:
        if mode == MODE_FAKE:
            from .fake import SatFakeCoordinator
            return SatFakeCoordinator(hass=hass, data=data, options=options)

        if mode == MODE_SIMULATOR:
            from .simulator import SatSimulatorCoordinator
            return SatSimulatorCoordinator(hass=hass, data=data, options=options)

        if mode == MODE_SWITCH:
            from .switch import SatSwitchCoordinator
            return SatSwitchCoordinator(hass=hass, entity_id=device, data=data, options=options)

        if mode == MODE_MQTT:
            from .mqtt import SatMqttCoordinator
            return await SatMqttCoordinator(hass=hass, device_id=device, data=data, options=options).boot()

        if mode == MODE_SERIAL:
            from .serial import SatSerialCoordinator
            return await SatSerialCoordinator(hass=hass, port=device, data=data, options=options).async_connect()

        raise Exception(f'Invalid mode[{mode}]')


class SatDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> None:
        """Initialize."""
        self.boiler_temperatures = []

        self._data = data
        self._options = options
        self._device_state = DeviceState.OFF
        self._simulation = bool(data.get(CONF_SIMULATION))
        self._heating_system = str(data.get(CONF_HEATING_SYSTEM, HEATING_SYSTEM_UNKNOWN))

        super().__init__(hass, _LOGGER, name=DOMAIN)

    @property
    def device_state(self):
        """Return the current state of the device."""
        return self._device_state

    @property
    def manufacturer(self) -> Manufacturer | None:
        if self.member_id is None:
            return None

        return ManufacturerFactory().resolve(self.member_id)

    @property
    @abstractmethod
    def setpoint(self) -> float | None:
        pass

    @property
    @abstractmethod
    def device_active(self) -> bool:
        pass

    @property
    @abstractmethod
    def member_id(self) -> int | None:
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
    def return_temperature(self) -> float | None:
        return None

    @property
    def filtered_boiler_temperature(self) -> float | None:
        # Not able to use if we do not have at least two values
        if len(self.boiler_temperatures) < 2:
            return None

        # Some noise filtering on the boiler temperature
        difference_boiler_temperature_sum = sum(
            abs(j[1] - i[1]) for i, j in zip(self.boiler_temperatures, self.boiler_temperatures[1:])
        )

        # Average it and return it
        return round(difference_boiler_temperature_sum / (len(self.boiler_temperatures) - 1), 2)

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
    def minimum_boiler_capacity(self) -> float | None:
        if (minimum_relative_modulation_value := self.minimum_relative_modulation_value) is None:
            return None

        if (boiler_capacity := self.boiler_capacity) is None:
            return None

        if boiler_capacity == 0:
            return 0

        return boiler_capacity * (minimum_relative_modulation_value / 100)

    @property
    def boiler_power(self) -> float | None:
        if (boiler_capacity := self.boiler_capacity) is None:
            return None

        if (minimum_boiler_capacity := self.minimum_boiler_capacity) is None:
            return None

        if (relative_modulation_value := self.relative_modulation_value) is None:
            return None

        if self.flame_active is False:
            return 0

        return minimum_boiler_capacity + ((boiler_capacity - minimum_boiler_capacity) * (relative_modulation_value / 100))

    @property
    def minimum_relative_modulation_value(self) -> float | None:
        return None

    @property
    def maximum_relative_modulation_value(self) -> float | None:
        return None

    @property
    def maximum_setpoint(self) -> float:
        """Return the maximum setpoint temperature that the device can support."""
        default_maximum_setpoint = calculate_default_maximum_setpoint(self._heating_system)
        return float(self._options.get(CONF_MAXIMUM_SETPOINT, default_maximum_setpoint))

    @property
    def minimum_setpoint(self) -> float:
        """Return the minimum setpoint temperature before the device starts to overshoot."""
        return float(self._data.get(CONF_MINIMUM_SETPOINT))

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
    def supports_relative_modulation_management(self):
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

    async def async_will_remove_from_hass(self, climate: SatClimate) -> None:
        """Run when an entity is removed from hass."""
        pass

    async def async_control_heating_loop(self, climate: SatClimate = None, _time=None) -> None:
        """Control the heating loop for the device."""
        if climate is not None and climate.hvac_mode == HVACMode.OFF and self.device_active:
            # Send out a new command to turn off the device
            await self.async_set_heater_state(DeviceState.OFF)

        current_time = datetime.now()

        # Make sure we have valid value
        if self.boiler_temperature is not None:
            self.boiler_temperatures.append((current_time, self.boiler_temperature))

        # Clear up any values that are older than the specified age
        while self.boiler_temperatures and current_time - self.boiler_temperatures[0][0] > timedelta(seconds=MAX_BOILER_TEMPERATURE_AGE):
            self.boiler_temperatures.pop()

    async def async_set_heater_state(self, state: DeviceState) -> None:
        """Set the state of the device heater."""
        self._device_state = state
        _LOGGER.info("Set central heater state %s", state)

    async def async_set_control_setpoint(self, value: float) -> None:
        """Control the boiler setpoint temperature for the device."""
        if self.supports_setpoint_management:
            _LOGGER.info("Set control boiler setpoint to %d", value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        """Control the DHW setpoint temperature for the device."""
        if self.supports_hot_water_setpoint_management:
            _LOGGER.info("Set control hot water setpoint to %d", value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        """Control the maximum setpoint temperature for the device."""
        if self.supports_maximum_setpoint_management:
            _LOGGER.info("Set maximum setpoint to %d", value)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        """Control the maximum relative modulation for the device."""
        if self.supports_relative_modulation_management:
            _LOGGER.info("Set maximum relative modulation to %d", value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        """Control the setpoint temperature for the thermostat."""
        pass
