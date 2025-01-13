from __future__ import annotations

import logging
from abc import abstractmethod
from enum import Enum
from time import monotonic
from typing import TYPE_CHECKING, Mapping, Any, Optional

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .boiler import BoilerTemperatureTracker
from .const import *
from .helpers import calculate_default_maximum_setpoint, seconds_since
from .manufacturer import Manufacturer, ManufacturerFactory

if TYPE_CHECKING:
    from .climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


class DeviceState(str, Enum):
    ON = "on"
    OFF = "off"


class DeviceStatus(str, Enum):
    HOT_WATER = "hot_water"
    PREHEATING = "preheating"
    HEATING_UP = "heating_up"
    AT_SETPOINT = "at_setpoint"
    COOLING_DOWN = "cooling_down"
    NEAR_SETPOINT = "near_setpoint"
    PUMP_STARTING = "pump_starting"
    WAITING_FOR_FLAME = "waiting_for_flame"
    OVERSHOOT_HANDLING = "overshoot_handling"
    OVERSHOOT_STABILIZED = "overshoot_stabilized"

    IDLE = "idle"
    UNKNOWN = "unknown"
    INITIALIZING = "initializing"


class SatDataUpdateCoordinatorFactory:
    @staticmethod
    def resolve(hass: HomeAssistant, mode: str, device: str, data: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> SatDataUpdateCoordinator:
        if mode == MODE_FAKE:
            from .fake import SatFakeCoordinator
            return SatFakeCoordinator(hass=hass, data=data, options=options)

        if mode == MODE_SIMULATOR:
            from .simulator import SatSimulatorCoordinator
            return SatSimulatorCoordinator(hass=hass, data=data, options=options)

        if mode == MODE_SWITCH:
            from .switch import SatSwitchCoordinator
            return SatSwitchCoordinator(hass=hass, entity_id=device, data=data, options=options)

        if mode == MODE_ESPHOME:
            from .esphome import SatEspHomeCoordinator
            return SatEspHomeCoordinator(hass=hass, device_id=device, data=data, options=options)

        if mode == MODE_MQTT_EMS:
            from .mqtt.ems import SatEmsMqttCoordinator
            return SatEmsMqttCoordinator(hass=hass, device_id=device, data=data, options=options)

        if mode == MODE_MQTT_OPENTHERM:
            from .mqtt.opentherm import SatOpenThermMqttCoordinator
            return SatOpenThermMqttCoordinator(hass=hass, device_id=device, data=data, options=options)

        if mode == MODE_SERIAL:
            from .serial import SatSerialCoordinator
            return SatSerialCoordinator(hass=hass, port=device, data=data, options=options)

        raise Exception(f'Invalid mode[{mode}]')


class SatDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> None:
        """Initialize."""
        self._boiler_temperature_cold: float | None = None
        self._boiler_temperatures: list[tuple[float, float]] = []
        self._boiler_temperature_tracker = BoilerTemperatureTracker()

        self._flame_on_since = None
        self._device_on_since = None

        self._data: Mapping[str, Any] = data
        self._options: Mapping[str, Any] = options or {}

        self._manufacturer: Manufacturer | None = None
        self._device_state: DeviceState = DeviceState.OFF
        self._simulation: bool = bool(self._options.get(CONF_SIMULATION))
        self._heating_system: str = str(data.get(CONF_HEATING_SYSTEM, HEATING_SYSTEM_UNKNOWN))

        if data.get(CONF_MANUFACTURER) is not None:
            self._manufacturer = ManufacturerFactory.resolve_by_name(data.get(CONF_MANUFACTURER))

        super().__init__(hass, _LOGGER, name=DOMAIN)

    @property
    @abstractmethod
    def device_id(self) -> str:
        pass

    @property
    @abstractmethod
    def device_type(self) -> str:
        pass

    @property
    def device_status(self):
        """Return the current status of the device."""
        if self.boiler_temperature is None:
            return DeviceStatus.INITIALIZING

        if self.hot_water_active:
            return DeviceStatus.HOT_WATER

        if self.setpoint is None or self.setpoint <= MINIMUM_SETPOINT:
            return DeviceStatus.IDLE

        if self.device_active:
            if self.boiler_temperature_cold is not None and self.boiler_temperature_cold > self.boiler_temperature:
                if self.boiler_temperature_derivative < 0:
                    return DeviceStatus.PUMP_STARTING

                if self._boiler_temperature_tracker.active and self.setpoint > self.boiler_temperature:
                    return DeviceStatus.PREHEATING

        if self.setpoint > self.boiler_temperature:
            if self.flame_active:
                if self._boiler_temperature_tracker.active:
                    return DeviceStatus.HEATING_UP

                return DeviceStatus.OVERSHOOT_HANDLING

            return DeviceStatus.WAITING_FOR_FLAME

        if abs(self.setpoint - self.boiler_temperature) <= DEADBAND:
            return DeviceStatus.AT_SETPOINT

        if self.boiler_temperature > self.setpoint:
            if self.flame_active:
                if self._boiler_temperature_tracker.active:
                    if self.boiler_temperature - self.setpoint > 2:
                        return DeviceStatus.HEATING_UP

                    return DeviceStatus.NEAR_SETPOINT

                return DeviceStatus.OVERSHOOT_HANDLING

            return DeviceStatus.WAITING_FOR_FLAME

        return DeviceStatus.UNKNOWN

    @property
    def manufacturer(self) -> Manufacturer | None:
        return self._manufacturer

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
    def flame_on_since(self) -> float | None:
        return self._flame_on_since

    @property
    def heater_on_since(self) -> float | None:
        return self._device_on_since

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
    def boiler_temperature_filtered(self) -> float | None:
        # Not able to use if we do not have at least two values
        if len(self._boiler_temperatures) < 2:
            return self.boiler_temperature

        # Some noise filtering on the boiler temperature
        difference_boiler_temperature_sum = sum(
            abs(j[1] - i[1]) for i, j in zip(self._boiler_temperatures, self._boiler_temperatures[1:])
        )

        # Average it and return it
        return round(difference_boiler_temperature_sum / (len(self._boiler_temperatures) - 1), 2)

    @property
    def boiler_temperature_derivative(self) -> float | None:
        if len(self._boiler_temperatures) <= 1:
            return None

        first_time, first_temperature = self._boiler_temperatures[-2]
        last_time, last_temperature = self._boiler_temperatures[-1]

        time_delta = last_time - first_time
        if time_delta <= 0:
            return None

        return round((last_temperature - first_temperature) / time_delta, 2)

    @property
    def boiler_temperature_cold(self) -> float | None:
        return self._boiler_temperature_cold

    @property
    def boiler_temperature_tracking(self) -> bool:
        return self._boiler_temperature_tracker.active

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

    async def async_setup(self) -> None:
        """Perform setup when the integration is about to be added to Home Assistant."""
        pass

    async def async_added_to_hass(self) -> None:
        """Perform setup when the integration is added to Home Assistant."""
        await self.async_set_control_max_setpoint(self.maximum_setpoint)

    async def async_will_remove_from_hass(self) -> None:
        """Run when an entity is removed from hass."""
        pass

    async def async_control_heating_loop(self, climate: Optional[SatClimate] = None, _time=None) -> None:
        """Control the heating loop for the device."""
        # Update Flame State
        if not self.flame_active:
            self._flame_on_since = None
        elif self._flame_on_since is None:
            self._flame_on_since = monotonic()

        # Update Device State
        if not self.device_active:
            self._device_on_since = None
        elif self._device_on_since is None:
            self._device_on_since = monotonic()

        # See if we can determine the manufacturer (deprecated)
        if self._manufacturer is None and self.member_id is not None:
            manufacturers = ManufacturerFactory.resolve_by_member_id(self.member_id)
            self._manufacturer = manufacturers[0] if len(manufacturers) > 0 else None

        # Nothing further to do without the temperature
        if self.boiler_temperature is None:
            return

        # Handle the temperature tracker
        if self.setpoint is not None and self.boiler_temperature_derivative is not None and self.device_status is not DeviceStatus.HOT_WATER:
            self._boiler_temperature_tracker.update(
                flame_active=self.flame_active,
                setpoint=round(self.setpoint, 0),
                boiler_temperature=round(self.boiler_temperature, 0),
                boiler_temperature_derivative=self.boiler_temperature_derivative
            )

        # Append current boiler temperature if valid and unique
        if self.boiler_temperature is not None:
            current_time = monotonic()
            if not self._boiler_temperatures or self._boiler_temperatures[-1][0] != current_time:
                self._boiler_temperatures.append((current_time, self.boiler_temperature))

        # Remove old temperature records beyond the allowed age
        self._boiler_temperatures = [
            (timestamp, temperature)
            for timestamp, temperature in self._boiler_temperatures
            if seconds_since(timestamp) <= MAX_BOILER_TEMPERATURE_AGE
        ]

        # Update the cold temperature of the boiler
        if boiler_temperature_cold := self._get_latest_boiler_cold_temperature():
            self._boiler_temperature_cold = boiler_temperature_cold

    async def async_set_heater_state(self, state: DeviceState) -> None:
        """Set the state of the device heater."""
        _LOGGER.info("Set central heater state %s", state)

    async def async_set_control_setpoint(self, value: float) -> None:
        """Control the boiler setpoint temperature for the device."""
        if self.supports_setpoint_management:
            _LOGGER.info("Set control boiler setpoint to %d°C", value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        """Control the DHW setpoint temperature for the device."""
        if self.supports_hot_water_setpoint_management:
            _LOGGER.info("Set control hot water setpoint to %d°C", value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        """Control the maximum setpoint temperature for the device."""
        if self.supports_maximum_setpoint_management:
            _LOGGER.info("Set maximum setpoint to %d°C", value)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        """Control the maximum relative modulation for the device."""
        if self.supports_relative_modulation_management:
            _LOGGER.info("Set maximum relative modulation to %d%%", value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        """Control the setpoint temperature for the thermostat."""
        pass

    def _is_flame_recent_or_cold_temperature_is_higher(self):
        """Check if the flame is recent or the cold temperature is higher than the boiler temperature."""
        if seconds_since(self._flame_on_since) <= 6:
            return True

        if self.boiler_temperature_cold is not None and self.boiler_temperature_cold > self.boiler_temperature:
            return True

        return False

    def _get_latest_boiler_cold_temperature(self) -> float | None:
        """Get the latest boiler cold temperature based on recent boiler temperatures."""
        for timestamp, temperature in reversed(self._boiler_temperatures):
            if self._device_on_since is None or self._device_on_since > timestamp:
                return temperature

            if self._flame_on_since is None or self._flame_on_since > timestamp:
                return temperature

        if self._boiler_temperature_cold is not None:
            return min(self.boiler_temperature, self._boiler_temperature_cold)

        return None


class SatEntityCoordinator(DataUpdateCoordinator):
    def get(self, domain: str, key: str) -> Optional[Any]:
        """Get the value for the given `key` from the boiler data.

        :param domain: Domain of where this value is located.
        :param key: Key of the value to retrieve from the boiler data.
        :return: Value for the given key from the boiler data, or None if the boiler data or the value are not available.
        """
        entity_id = self._get_entity_id(domain, key)
        if entity_id is None:
            return None

        state = self.hass.states.get(self._get_entity_id(domain, key))
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None

        return state.state

    @abstractmethod
    def _get_entity_id(self, domain: str, key: str):
        pass
