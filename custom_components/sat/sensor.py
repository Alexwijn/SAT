from __future__ import annotations

import logging
import typing

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_MODE, MODE_SERIAL, CONF_NAME, DOMAIN, COORDINATOR, CLIMATE, MODE_SIMULATOR, CONF_MINIMUM_CONSUMPTION, CONF_MAXIMUM_CONSUMPTION
from .coordinator import SatDataUpdateCoordinator
from .entity import SatEntity
from .serial import sensor as serial_sensor
from .simulator import sensor as simulator_sensor

if typing.TYPE_CHECKING:
    from .climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_entities: AddEntitiesCallback):
    """
    Add sensors for the serial protocol if the integration is set to use it.
    """
    climate = _hass.data[DOMAIN][_config_entry.entry_id][CLIMATE]
    coordinator = _hass.data[DOMAIN][_config_entry.entry_id][COORDINATOR]

    # Check if integration is set to use the serial protocol
    if _config_entry.data.get(CONF_MODE) == MODE_SERIAL:
        await serial_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)

    # Check if integration is set to use the simulator
    if _config_entry.data.get(CONF_MODE) == MODE_SIMULATOR:
        await simulator_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)

    _async_add_entities([
        SatErrorValueSensor(coordinator, _config_entry, climate),
        SatHeatingCurveSensor(coordinator, _config_entry, climate),
    ])

    if coordinator.supports_relative_modulation_management:
        _async_add_entities([SatCurrentPowerSensor(coordinator, _config_entry)])

        if float(_config_entry.options.get(CONF_MINIMUM_CONSUMPTION) or 0) > 0 and float(_config_entry.options.get(CONF_MAXIMUM_CONSUMPTION) or 0) > 0:
            _async_add_entities([SatCurrentConsumptionSensor(coordinator, _config_entry)])


class SatCurrentPowerSensor(SatEntity, SensorEntity):

    @property
    def name(self) -> str:
        return f"Boiler Current Power {self._config_entry.data.get(CONF_NAME)} (Boiler)"

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
        return f"Boiler Current Consumption {self._config_entry.data.get(CONF_NAME)} (Boiler)"

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

        if self._coordinator.device_active is False:
            return 0

        if self._coordinator.flame_active is False:
            return self._minimum_consumption

        gas_consumption_per_percentage = (self._maximum_consumption - self._minimum_consumption) / 100
        relative_modulation_value = self._coordinator.relative_modulation_value

        return round(relative_modulation_value * gas_consumption_per_percentage, 3)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-boiler-current-consumption"


class SatHeatingCurveSensor(SatEntity, SensorEntity):

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, climate: SatClimate):
        super().__init__(coordinator, config_entry)

        self._climate = climate

    async def async_added_to_hass(self) -> None:
        async def on_state_change(_event: Event):
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
        return self._climate.extra_state_attributes.get("heating_curve") is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current heating curve value.
        """
        return self._climate.extra_state_attributes.get("heating_curve")

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-heating-curve"


class SatErrorValueSensor(SatEntity, SensorEntity):

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, climate: SatClimate):
        super().__init__(coordinator, config_entry)

        self._climate = climate

    async def async_added_to_hass(self) -> None:
        async def on_state_change(_event: Event):
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
    def available(self):
        """Return availability of the sensor."""
        return self._climate.extra_state_attributes.get("error") is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current error value.
        """
        return self._climate.extra_state_attributes.get("error")

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-error-value"
