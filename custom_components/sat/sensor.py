from __future__ import annotations

import logging
import typing

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_MODE, MODE_SERIAL, CONF_NAME, DOMAIN, COORDINATOR, CLIMATE, MODE_SIMULATOR
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
        return self._coordinator.relative_modulation_value is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current capacity of the boiler in kW.
        """
        # If the flame is off, return 0 kW
        if self._coordinator.flame_active is False:
            return 0

        # Get the relative modulation level from the data
        relative_modulation = self._coordinator.relative_modulation_value

        # Get the boiler capacity from the data
        if (boiler_capacity := self._coordinator.boiler_capacity) == 0:
            return 0

        # Get and calculate the minimum capacity from the data
        minimum_capacity = boiler_capacity / (100 / self._coordinator.minimum_relative_modulation_value)

        # Calculate and return the current capacity in kW
        return minimum_capacity + (((boiler_capacity - minimum_capacity) / 100) * relative_modulation)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-boiler-current-power"


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
