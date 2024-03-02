from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.components.climate import HVACAction
from homeassistant.components.group.binary_sensor import BinarySensorGroup
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .climate import SatClimate
from .const import CONF_MODE, MODE_SERIAL, CONF_NAME, DOMAIN, COORDINATOR, CLIMATE, CONF_WINDOW_SENSORS
from .entity import SatClimateEntity
from .serial import binary_sensor as serial_binary_sensor

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_entities: AddEntitiesCallback):
    """
    Add binary sensors for the serial protocol if the integration is set to use it.
    """
    climate = _hass.data[DOMAIN][_config_entry.entry_id][CLIMATE]
    coordinator = _hass.data[DOMAIN][_config_entry.entry_id][COORDINATOR]

    # Check if integration is set to use the serial protocol
    if _config_entry.data.get(CONF_MODE) == MODE_SERIAL:
        await serial_binary_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)

    if coordinator.supports_setpoint_management:
        _async_add_entities([SatControlSetpointSynchroSensor(coordinator, _config_entry, climate)])

    if coordinator.supports_relative_modulation_management:
        _async_add_entities([SatRelativeModulationSynchroSensor(coordinator, _config_entry, climate)])

    if len(_config_entry.options.get(CONF_WINDOW_SENSORS, [])) > 0:
        _async_add_entities([SatWindowSensor(coordinator, _config_entry, climate)])

    _async_add_entities([SatCentralHeatingSynchroSensor(coordinator, _config_entry, climate)])


class SatControlSetpointSynchroSensor(SatClimateEntity, BinarySensorEntity):

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return "Control Setpoint Synchro"

    @property
    def device_class(self):
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._climate.setpoint is not None and self._coordinator.setpoint is not None

    @property
    def is_on(self):
        """Return the state of the sensor."""
        return round(self._climate.setpoint, 1) != round(self._coordinator.setpoint, 1)

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-control-setpoint-synchro"


class SatRelativeModulationSynchroSensor(SatClimateEntity, BinarySensorEntity):

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return "Relative Modulation Synchro"

    @property
    def device_class(self):
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._climate.relative_modulation_value is not None and self._coordinator.maximum_relative_modulation_value is not None

    @property
    def is_on(self):
        """Return the state of the sensor."""
        return int(self._climate.relative_modulation_value) != int(self._coordinator.maximum_relative_modulation_value)

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-relative-modulation-synchro"


class SatCentralHeatingSynchroSensor(SatClimateEntity, BinarySensorEntity):

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Central Heating Synchro"

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._climate is not None

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        device_active = self._coordinator.device_active
        climate_hvac_action = self._climate.state_attributes.get("hvac_action")

        return not (
                (climate_hvac_action == HVACAction.OFF and not device_active) or
                (climate_hvac_action == HVACAction.IDLE and not device_active) or
                (climate_hvac_action == HVACAction.HEATING and device_active)
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-central-heating-synchro"


class SatWindowSensor(SatClimateEntity, BinarySensorGroup):
    def __init__(self, coordinator, config_entry: ConfigEntry, climate: SatClimate):
        super().__init__(coordinator, config_entry, climate)

        self.mode = any
        self._entity_ids = self._config_entry.options.get(CONF_WINDOW_SENSORS)
        self._attr_extra_state_attributes = {ATTR_ENTITY_ID: self._entity_ids}

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Smart Autotune Thermostat Window Sensor"

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return BinarySensorDeviceClass.WINDOW

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-window-sensor"
