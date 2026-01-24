from __future__ import annotations

import asyncio
from collections import deque
from typing import Optional

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.climate import HVACAction
from homeassistant.components.group.binary_sensor import BinarySensorGroup
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SatHeatingControl
from .climate import SatClimate
from .const import *
from .coordinator.serial import binary_sensor as serial_binary_sensor
from .entity import SatClimateEntity, SatEntity
from .entry_data import SatConfig, SatMode, get_entry_data
from .helpers import float_value, seconds_since, timestamp
from .types import BoilerStatus, CycleClassification

PRESSURE_DROP_RATE_SETTLE_SECONDS = 600


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
        SatPressureHealthSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
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
        return self._heating_control.control_setpoint is not None and self._coordinator.setpoint is not None

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        climate_setpoint = round(self._heating_control.control_setpoint, 1)
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


class SatPressureHealthSensor(SatEntity, RestoreEntity, BinarySensorEntity):
    def __init__(self, coordinator, config: SatConfig, heating_control: SatHeatingControl):
        super().__init__(coordinator, config, heating_control)
        self._pressure_config = self._config.pressure_health

        self._last_active: Optional[bool] = None
        self._drop_rate_suspended_until: Optional[float] = None
        self._last_pressure: Optional[float] = None
        self._last_drop_rate: Optional[float] = None
        self._last_seen_pressure: Optional[float] = None
        self._last_pressure_timestamp: Optional[float] = None
        self._pressure_samples: deque[tuple[float, float]] = deque()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None:
            return

        attributes = last_state.attributes or {}
        self._last_pressure = float_value(attributes.get("last_pressure"))
        self._last_pressure_timestamp = float_value(attributes.get("last_pressure_timestamp"))
        self._last_seen_pressure = float_value(attributes.get("last_seen_pressure_timestamp"))

        if self._last_pressure is not None and self._last_pressure_timestamp is not None:
            self._pressure_samples.append((self._last_pressure_timestamp, self._last_pressure))

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Pressure Health"

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        if self._coordinator.boiler_pressure is not None:
            return True

        if self._last_seen_pressure is None:
            return False

        return True

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        now = timestamp()
        pressure = self._coordinator.boiler_pressure
        minimum_pressure = self._pressure_config.minimum_pressure_bar
        maximum_pressure = self._pressure_config.maximum_pressure_bar
        maximum_age_seconds = self._pressure_config.maximum_age_seconds
        maximum_drop_rate = self._pressure_config.maximum_drop_rate_bar_per_hour

        self._track_active_state(now)

        if pressure is None:
            if self._last_seen_pressure is None:
                return False

            if maximum_age_seconds <= 0:
                return False

            return (now - self._last_seen_pressure) > maximum_age_seconds

        self._last_seen_pressure = now
        self._record_pressure_sample(now, pressure, maximum_age_seconds)

        drop_rate = self._calculate_drop_rate()

        self._last_pressure = pressure
        self._last_pressure_timestamp = now

        pressure_low = pressure < minimum_pressure
        pressure_high = pressure > maximum_pressure
        drop_rate_allowed = self._drop_rate_allowed(now)

        if not drop_rate_allowed:
            drop_rate = None
            self._last_drop_rate = None
        elif drop_rate is not None:
            self._last_drop_rate = round(drop_rate, 3)

        drop_rate_high = drop_rate is not None and drop_rate > maximum_drop_rate

        return pressure_low or pressure_high or drop_rate_high

    @property
    def extra_state_attributes(self) -> dict[str, Optional[float]]:
        """Return extra attributes for debugging pressure health decisions."""
        return {
            "pressure": self._coordinator.boiler_pressure,
            "pressure_drop_rate_bar_per_hour": self._last_drop_rate,

            "last_pressure": self._last_pressure,
            "last_pressure_timestamp": self._last_pressure_timestamp,
            "last_seen_pressure_timestamp": self._last_seen_pressure,
        }

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-pressure-health"

    def _track_active_state(self, timestamp_seconds: float) -> None:
        active = self._coordinator.active
        if self._last_active is None:
            self._last_active = active
            return

        if self._last_active and not active:
            self._drop_rate_suspended_until = timestamp_seconds + PRESSURE_DROP_RATE_SETTLE_SECONDS
            self._pressure_samples.clear()

        self._last_active = active

    def _drop_rate_allowed(self, timestamp_seconds: float) -> bool:
        if self._drop_rate_suspended_until is None:
            return True

        if timestamp_seconds >= self._drop_rate_suspended_until:
            self._drop_rate_suspended_until = None
            return True

        return False

    def _record_pressure_sample(self, timestamp_seconds: float, pressure: float, maximum_age_seconds: float) -> None:
        if maximum_age_seconds <= 0:
            maximum_age_seconds = 3600

        drop_window_seconds = max(maximum_age_seconds, 3600)
        self._pressure_samples.append((timestamp_seconds, pressure))

        while self._pressure_samples and (timestamp_seconds - self._pressure_samples[0][0]) > drop_window_seconds:
            self._pressure_samples.popleft()

    def _calculate_drop_rate(self) -> Optional[float]:
        if len(self._pressure_samples) < 2:
            return None

        oldest_time, oldest_pressure = self._pressure_samples[0]
        newest_time, newest_pressure = self._pressure_samples[-1]
        elapsed = newest_time - oldest_time

        if elapsed <= 0:
            return None

        return ((oldest_pressure - newest_pressure) / elapsed) * 3600


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
