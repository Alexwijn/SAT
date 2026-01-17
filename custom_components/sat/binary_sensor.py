from __future__ import annotations

import asyncio
from typing import Optional

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.climate import HVACAction
from homeassistant.components.group.binary_sensor import BinarySensorGroup
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SatHeatingControl
from .climate import SatClimate
from .const import *
from .coordinator.serial import binary_sensor as serial_binary_sensor
from .entity import SatClimateEntity, SatEntity
from .entry_data import SatConfig, SatMode, get_entry_data
from .helpers import seconds_since, timestamp
from .types import BoilerStatus


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add SAT binary sensors based on the configured integration mode."""
    if config_entry.entry_id not in hass.data.get(DOMAIN, {}):
        return

    entry_data = get_entry_data(hass, config_entry.entry_id)
    if entry_data.climate is None:
        try:
            await asyncio.wait_for(entry_data.climate_ready.wait(), timeout=10)
        except asyncio.TimeoutError:
            return

        if entry_data.climate is None:
            return

    if entry_data.config.mode == SatMode.SERIAL:
        await serial_binary_sensor.async_setup_entry(hass, config_entry, async_add_entities)

    sensors: list[BinarySensorEntity] = [
        SatCycleHealthSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatDeviceHealthSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatCentralHeatingSyncSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
    ]

    if len(entry_data.config.window_sensors) > 0:
        sensors.append(SatWindowSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))

    if entry_data.coordinator.supports_setpoint_management:
        sensors.append(SatControlSetpointSyncSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))

    if entry_data.coordinator.supports_relative_modulation_management:
        sensors.append(SatRelativeModulationSyncSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))

    async_add_entities(sensors)


class SatSyncSensor:
    """Mixin to add delayed state change for binary sensors."""

    def __init__(self, delay: int = 60):
        """Initialize the mixin with a delay."""
        self._delay = delay
        self._last_mismatch: Optional[float] = None

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


class SatControlSetpointSyncSensor(SatSyncSensor, SatEntity, BinarySensorEntity):
    def __init__(self, coordinator, config: SatConfig, heating_control: SatHeatingControl):
        SatSyncSensor.__init__(self)
        SatEntity.__init__(self, coordinator, config, heating_control)

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Control Setpoint Synchronization"

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._heating_control.setpoint is not None and self._coordinator.setpoint is not None

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        climate_setpoint = round(self._heating_control.setpoint, 1)
        coordinator_setpoint = round(self._coordinator.setpoint, 1)

        return self.state_delayed(climate_setpoint != coordinator_setpoint)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-control-setpoint-synchro"


class SatRelativeModulationSyncSensor(SatSyncSensor, SatEntity, BinarySensorEntity):
    def __init__(self, coordinator, config: SatConfig, heating_control: SatHeatingControl):
        SatSyncSensor.__init__(self)
        SatEntity.__init__(self, coordinator, config, heating_control)

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Relative Modulation Synchronization"

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        climate_modulation = self._heating_control.relative_modulation_value
        maximum_modulation = self._coordinator.maximum_relative_modulation_value

        return climate_modulation is not None and maximum_modulation is not None

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        climate_modulation = int(self._heating_control.relative_modulation_value)
        maximum_modulation = int(self._coordinator.maximum_relative_modulation_value)

        return self.state_delayed(climate_modulation != maximum_modulation)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-relative-modulation-synchro"


class SatCentralHeatingSyncSensor(SatSyncSensor, SatClimateEntity, BinarySensorEntity):
    def __init__(self, coordinator, config: SatConfig, heating_control: SatHeatingControl, climate: SatClimate):
        SatSyncSensor.__init__(self)
        SatClimateEntity.__init__(self, coordinator, config, heating_control, climate)

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Central Heating Synchronization"

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._climate is not None

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        device_active = self._coordinator.active
        climate_hvac_action = self._climate.state_attributes.get("hvac_action")

        should_be_off = climate_hvac_action == HVACAction.OFF and not device_active
        should_be_idle = climate_hvac_action == HVACAction.IDLE and not device_active
        should_be_heating = climate_hvac_action == HVACAction.HEATING and device_active

        return self.state_delayed(not (should_be_off or should_be_idle or should_be_heating))

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-central-heating-synchro"


class SatDeviceHealthSensor(SatEntity, BinarySensorEntity):

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Device Health"

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return self._heating_control.device_status == BoilerStatus.INSUFFICIENT_DATA

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-boiler-health"


class SatCycleHealthSensor(SatEntity, BinarySensorEntity):
    async def async_added_to_hass(self) -> None:
        def on_cycle_event(_event: Event) -> None:
            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_SAT_CYCLE_ENDED, on_cycle_event))

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Cycle Health"

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        if self._heating_control is None or self._heating_control.last_cycle is None:
            return False

        return self._heating_control.last_cycle.classification not in (
            CycleClassification.GOOD,
            CycleClassification.UNCERTAIN,
            CycleClassification.INSUFFICIENT_DATA,
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-cycle-health"


class SatWindowSensor(SatEntity, BinarySensorGroup):
    def __init__(self, coordinator, config: SatConfig, heating_control: SatHeatingControl):
        super().__init__(coordinator, config, heating_control)

        self.mode = any
        self._entity_ids = self._config.window_sensors
        self._attr_extra_state_attributes = {ATTR_ENTITY_ID: self._entity_ids}

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Window Sensor"

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        """Return the device class."""
        return BinarySensorDeviceClass.WINDOW

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-window-sensor"
