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
from .const import CONF_MODE, MODE_SERIAL, CONF_NAME, DOMAIN, COORDINATOR, CLIMATE, CONF_WINDOW_SENSORS, CycleClassification
from .entity import SatClimateEntity, SatEntity
from .helpers import seconds_since, timestamp
from .serial import binary_sensor as serial_binary_sensor
from .types import BoilerStatus

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_entities: AddEntitiesCallback):
    """
    Add binary sensors for the serial protocol if the integration is set to use it.
    """
    # Some sanity checks before we continue
    if any(key not in _hass.data[DOMAIN][_config_entry.entry_id] for key in (CLIMATE, COORDINATOR)):
        return

    climate = _hass.data[DOMAIN][_config_entry.entry_id][CLIMATE]
    coordinator = _hass.data[DOMAIN][_config_entry.entry_id][COORDINATOR]

    # Check if integration is set to use the serial protocol
    if _config_entry.data.get(CONF_MODE) == MODE_SERIAL:
        await serial_binary_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)

    if coordinator.supports_setpoint_management:
        _async_add_entities([SatControlSetpointSyncSensor(coordinator, _config_entry, climate)])

    if coordinator.supports_relative_modulation_management:
        _async_add_entities([SatRelativeModulationSyncSensor(coordinator, _config_entry, climate)])

    if len(_config_entry.options.get(CONF_WINDOW_SENSORS, [])) > 0:
        _async_add_entities([SatWindowSensor(coordinator, _config_entry, climate)])

    _async_add_entities([
        SatCycleHealthSensor(coordinator, _config_entry),
        SatBoilerHealthSensor(coordinator, _config_entry),
        SatCentralHeatingSyncSensor(coordinator, _config_entry, climate)
    ])


class SatSyncSensor:
    """Mixin to add delayed state change for binary sensors."""

    def __init__(self, delay: int = 60):
        """Initialize the mixin with a delay."""
        self._delay = delay
        self._last_mismatch = None

    def state_delayed(self, condition: bool) -> bool:
        """Determine the delayed state based on a condition."""
        if not condition:
            self._last_mismatch = None
            return False

        if self._last_mismatch is None:
            self._last_mismatch = timestamp()

        if seconds_since(self._last_mismatch) >= self._delay:
            return True

        return False


class SatControlSetpointSyncSensor(SatSyncSensor, SatClimateEntity, BinarySensorEntity):
    def __init__(self, coordinator, _config_entry, climate):
        SatSyncSensor.__init__(self)
        SatClimateEntity.__init__(self, coordinator, _config_entry, climate)

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return "Control Setpoint Synchronization"

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
        return self.state_delayed(round(self._climate.setpoint, 1) != round(self._coordinator.setpoint, 1))

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-control-setpoint-synchro"


class SatRelativeModulationSyncSensor(SatSyncSensor, SatClimateEntity, BinarySensorEntity):
    def __init__(self, coordinator, _config_entry, climate):
        SatSyncSensor.__init__(self)
        SatClimateEntity.__init__(self, coordinator, _config_entry, climate)

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return "Relative Modulation Synchronization"

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
        return self.state_delayed(int(self._climate.relative_modulation_value) != int(self._coordinator.maximum_relative_modulation_value))

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-relative-modulation-synchro"


class SatCentralHeatingSyncSensor(SatSyncSensor, SatClimateEntity, BinarySensorEntity):
    def __init__(self, coordinator, _config_entry, climate):
        SatSyncSensor.__init__(self)
        SatClimateEntity.__init__(self, coordinator, _config_entry, climate)

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Central Heating Synchronization"

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

        return self.state_delayed(not (
                (climate_hvac_action == HVACAction.OFF and not device_active) or
                (climate_hvac_action == HVACAction.IDLE and not device_active) or
                (climate_hvac_action == HVACAction.HEATING and device_active)
        ))

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-central-heating-synchro"


class SatBoilerHealthSensor(SatEntity, BinarySensorEntity):

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Boiler Health"

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return self._coordinator.device_status == BoilerStatus.INSUFFICIENT_DATA

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-boiler-health"


class SatCycleHealthSensor(SatEntity, BinarySensorEntity):

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Cycle Health"

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        if self._coordinator.last_cycle is None:
            return False

        return self._coordinator.last_cycle.classification not in (CycleClassification.GOOD, CycleClassification.UNCERTAIN, CycleClassification.INSUFFICIENT_DATA)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-cycle-health"


class SatWindowSensor(SatClimateEntity, BinarySensorGroup):
    def __init__(self, coordinator, config_entry: ConfigEntry, climate: SatClimate):
        super().__init__(coordinator, config_entry, climate)

        self.mode = any
        self._entity_ids = self._config_entry.options.get(CONF_WINDOW_SENSORS)
        self._attr_extra_state_attributes = {ATTR_ENTITY_ID: self._entity_ids}

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Window Sensor"

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return BinarySensorDeviceClass.WINDOW

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-window-sensor"
