from __future__ import annotations

import logging
from typing import Mapping, Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant, Event, EventStateChangedData
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .climate import SatClimate
from .const import *
from .coordinator import SatDataUpdateCoordinator
from .entity import SatEntity, SatClimateEntity
from .serial import sensor as serial_sensor
from .simulator import sensor as simulator_sensor

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_entities: AddEntitiesCallback):
    """
    Add sensors for the serial protocol if the integration is set to use it.
    """
    # Some sanity checks before we continue
    if any(key not in _hass.data[DOMAIN][_config_entry.entry_id] for key in (CLIMATE, COORDINATOR)):
        return

    climate = _hass.data[DOMAIN][_config_entry.entry_id][CLIMATE]
    coordinator = _hass.data[DOMAIN][_config_entry.entry_id][COORDINATOR]

    # Check if integration is set to use the serial protocol
    if _config_entry.data.get(CONF_MODE) == MODE_SERIAL:
        await serial_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)

    # Check if integration is set to use the simulator
    if _config_entry.data.get(CONF_MODE) == MODE_SIMULATOR:
        await simulator_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)

    _async_add_entities([
        SatCycleSensor(coordinator, _config_entry),
        SatBoilerSensor(coordinator, _config_entry),
        SatManufacturerSensor(coordinator, _config_entry),
        SatPidSensor(coordinator, _config_entry, climate),
        SatErrorValueSensor(coordinator, _config_entry, climate),
        SatRequestedSetpoint(coordinator, _config_entry, climate),
        SatHeatingCurveSensor(coordinator, _config_entry, climate),
    ])

    for entity_id in _config_entry.data.get(CONF_ROOMS) or []:
        _async_add_entities([SatPidSensor(coordinator, _config_entry, climate, entity_id)])

    if coordinator.supports_relative_modulation_management:
        _async_add_entities([SatCurrentPowerSensor(coordinator, _config_entry)])

        if float(_config_entry.options.get(CONF_MINIMUM_CONSUMPTION) or 0) > 0 and float(_config_entry.options.get(CONF_MAXIMUM_CONSUMPTION) or 0) > 0:
            _async_add_entities([SatCurrentConsumptionSensor(coordinator, _config_entry)])


class SatRequestedSetpoint(SatClimateEntity, SensorEntity):

    @property
    def name(self) -> str:
        return f"Requested Setpoint {self._config_entry.data.get(CONF_NAME)}"

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float:
        return self._climate.requested_setpoint

    @property
    def unique_id(self) -> str:
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-requested-setpoint"


class SatPidSensor(SatClimateEntity, SensorEntity):
    def __init__(self, coordinator, config_entry: ConfigEntry, climate: SatClimate, area_id: str = None):
        super().__init__(coordinator, config_entry, climate)

        self._area_id: Optional[str] = area_id

    @property
    def _pid(self):
        if self._area_id is None:
            return self._climate.pid

        if (area := self._climate.areas.get(self._area_id)) is None:
            return None

        return area.pid

    @property
    def name(self) -> str:
        if self._area_id is None:
            return f"PID {self._config_entry.data.get(CONF_NAME)}"

        return f"PID {self._config_entry.data.get(CONF_NAME)} ({self._area_id})"

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def available(self):
        return self._pid.available if self._pid is not None else False

    @property
    def native_value(self) -> float:
        return self._pid.output

    @property
    def unique_id(self) -> str:
        if self._area_id is None:
            return f"{self._config_entry.data.get(CONF_NAME).lower()}-pid"

        return f"{self._config_entry.data.get(CONF_NAME).lower()}-{self._area_id}-pid"

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        return {
            "proportional": self._pid.proportional,
            "integral": self._pid.integral,
            "derivative": self._pid.derivative,
        }


class SatCurrentPowerSensor(SatEntity, SensorEntity):

    @property
    def name(self) -> str:
        return f"Current Power {self._config_entry.data.get(CONF_NAME)} (Boiler)"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.POWER

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfPower.KILO_WATT

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.boiler_power is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current power of the boiler in kW.
        """
        return self._coordinator.boiler_power

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-boiler-current-power"


class SatCurrentConsumptionSensor(SatEntity, SensorEntity):

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)

        self._minimum_consumption = self._config_entry.options.get(CONF_MINIMUM_CONSUMPTION)
        self._maximum_consumption = self._config_entry.options.get(CONF_MAXIMUM_CONSUMPTION)

    @property
    def name(self) -> str:
        return f"Current Consumption {self._config_entry.data.get(CONF_NAME)} (Boiler)"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.GAS

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfVolume.CUBIC_METERS

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.relative_modulation_value is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current consumption of the boiler in m³/h.
        """

        if not self._coordinator.device_active:
            return 0

        if not self._coordinator.flame_active:
            return 0

        differential_gas_consumption = self._maximum_consumption - self._minimum_consumption
        relative_modulation_value = self._coordinator.relative_modulation_value

        return round(self._minimum_consumption + ((relative_modulation_value / 100) * differential_gas_consumption), 3)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-boiler-current-consumption"


class SatHeatingCurveSensor(SatClimateEntity, SensorEntity):

    async def async_added_to_hass(self) -> None:
        async def on_state_change(_event: Event[EventStateChangedData]):
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._climate.entity_id], on_state_change
            )
        )

    @property
    def name(self) -> str:
        return f"Heating Curve {self._config_entry.data.get(CONF_NAME)}"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._climate.heating_curve.value is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units."""
        return self._climate.heating_curve.value

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-heating-curve"


class SatErrorValueSensor(SatClimateEntity, SensorEntity):

    async def async_added_to_hass(self) -> None:
        async def on_state_change(_event: Event[EventStateChangedData]):
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._climate.entity_id], on_state_change
            )
        )

    @property
    def name(self) -> str:
        return f"Error Value {self._config_entry.data.get(CONF_NAME)}"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units."""
        return self._climate.error.value

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._climate.error is not None

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-error-value"


class SatManufacturerSensor(SatEntity, SensorEntity):
    @property
    def name(self) -> str:
        return f"Boiler Manufacturer {self._config_entry.data.get(CONF_NAME)}"

    @property
    def native_value(self) -> str:
        manufacturer = self._coordinator.manufacturer
        return manufacturer.friendly_name if manufacturer is not None else None

    @property
    def available(self) -> bool:
        return self._coordinator.manufacturer is not None

    @property
    def unique_id(self) -> str:
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-manufacturer"


class SatCycleSensor(SatEntity, SensorEntity):
    @property
    def name(self) -> str:
        return f"Cycle Status {self._config_entry.data.get(CONF_NAME)}"

    @property
    def native_value(self) -> str:
        if self._coordinator.last_cycle is None:
            return CycleClassification.INSUFFICIENT_DATA.name

        return self._coordinator.last_cycle.classification.name

    @property
    def unique_id(self) -> str:
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-cycle-status"


class SatBoilerSensor(SatEntity, SensorEntity):
    @property
    def name(self) -> str:
        return f"Boiler Status {self._config_entry.data.get(CONF_NAME)}"

    @property
    def native_value(self) -> str:
        return self._coordinator.device_status.name

    @property
    def available(self) -> bool:
        return self._coordinator.device_status != BoilerStatus.INSUFFICIENT_DATA

    @property
    def unique_id(self) -> str:
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-boiler-status"
