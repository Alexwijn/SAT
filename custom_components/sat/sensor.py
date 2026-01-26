from __future__ import annotations

import asyncio
from typing import Optional, Mapping, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature, UnitOfVolume
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .climate import SatClimate
from .const import *
from .coordinator import SatDataUpdateCoordinator
from .coordinator.serial import sensor as serial_sensor
from .coordinator.simulator import sensor as simulator_sensor
from .entity import SatClimateEntity, SatEntity
from .entry_data import SatConfig, SatMode, get_entry_data
from .heating_control import SatHeatingControl
from .types import BoilerStatus, CycleClassification


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add SAT sensors based on the configured integration mode."""
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
        await serial_sensor.async_setup_entry(hass, config_entry, async_add_entities)

    if entry_data.config.mode == SatMode.SIMULATOR:
        await simulator_sensor.async_setup_entry(hass, config_entry, async_add_entities)

    sensors: list[SensorEntity] = [
        SatPidSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
        SatCycleSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatDeviceSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatManufacturerSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatErrorValueSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
        SatRequestedSetpoint(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
        SatHeatingCurveSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
    ]

    for entity_id in entry_data.config.rooms:
        sensors.append(SatPidSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate, entity_id))

    if entry_data.coordinator.supports_relative_modulation_management:
        sensors.append(SatCurrentPowerSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))

        minimum_consumption = entry_data.config.limits.minimum_consumption
        maximum_consumption = entry_data.config.limits.maximum_consumption

        if minimum_consumption > 0 and maximum_consumption > 0:
            sensors.append(SatCurrentConsumptionSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))

    async_add_entities(sensors)


class SatRequestedSetpoint(SatClimateEntity, SensorEntity):
    async def async_added_to_hass(self) -> None:
        def on_pid_updated(entity_id: str) -> None:
            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

    @property
    def name(self) -> str:
        return "Requested Setpoint"

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> Optional[float]:
        return self._climate.requested_setpoint

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-requested-setpoint"


class SatPidSensor(SatClimateEntity, SensorEntity):
    def __init__(
            self,
            coordinator: SatDataUpdateCoordinator,
            config: SatConfig,
            heating_control: SatHeatingControl,
            climate: SatClimate,
            area_id: Optional[str] = None,
    ):
        super().__init__(coordinator, config, heating_control, climate)
        self._area_id: Optional[str] = area_id

    async def async_added_to_hass(self) -> None:
        def on_pid_updated(entity_id: str) -> None:
            if entity_id != self._signal_entity_id:
                return

            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self._signal_entity_id = self._area_id or self._climate.entity_id
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

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
            return f"PID"

        return f"PID ({self._area_id})"

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def available(self):
        if (pid := self._pid) is None:
            return False

        return pid.available

    @property
    def native_value(self) -> Optional[float]:
        if (pid := self._pid) is None:
            return None

        return pid.output

    @property
    def unique_id(self) -> str:
        if self._area_id is None:
            return f"{self._config.entry_id}-pid"

        return f"{self._config.entry_id}-{self._area_id}-pid"

    @property
    def extra_state_attributes(self) -> Optional[Mapping[str, Any]]:
        if (pid := self._pid) is None:
            return None

        return {
            "error": pid.last_error,
            "proportional": pid.proportional,
            "integral": pid.integral,
            "derivative": pid.derivative,
        }


class SatCurrentPowerSensor(SatEntity, SensorEntity):

    @property
    def name(self) -> str:
        return "Current Power (Device)"

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
        return f"{self._config.entry_id}-boiler-current-power"


class SatCurrentConsumptionSensor(SatEntity, SensorEntity):

    def __init__(self, coordinator: SatDataUpdateCoordinator, config: SatConfig, heating_control: SatHeatingControl):
        super().__init__(coordinator, config, heating_control)

        self._minimum_consumption = self._config.limits.minimum_consumption
        self._maximum_consumption = self._config.limits.maximum_consumption

    @property
    def name(self) -> str:
        return "Current Consumption (Device)"

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
        if not self._coordinator.active or not self._coordinator.flame_active:
            return 0.0

        differential_gas_consumption = self._maximum_consumption - self._minimum_consumption
        relative_modulation_value = self._coordinator.relative_modulation_value

        modulation_fraction = relative_modulation_value / 100
        return round(self._minimum_consumption + (modulation_fraction * differential_gas_consumption), 3)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-boiler-current-consumption"


class SatHeatingCurveSensor(SatClimateEntity, SensorEntity):

    @property
    def name(self) -> str:
        return "Heating Curve"

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
    def native_value(self) -> Optional[float]:
        """Return the state of the device in native units."""
        return self._climate.heating_curve.value

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-heating-curve"


class SatErrorValueSensor(SatClimateEntity, SensorEntity):

    @property
    def name(self) -> str:
        return "Error Value"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the device in native units."""
        if (error := self._climate.error) is None:
            return None

        return error.error

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._climate.error is not None

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-error-value"


class SatManufacturerSensor(SatEntity, SensorEntity):
    @property
    def name(self) -> str:
        return "Device Manufacturer"

    @property
    def native_value(self) -> Optional[str]:
        manufacturer = self._coordinator.manufacturer
        return manufacturer.friendly_name if manufacturer is not None else None

    @property
    def available(self) -> bool:
        return self._coordinator.manufacturer is not None

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-manufacturer"


class SatCycleSensor(SatEntity, SensorEntity):
    async def async_added_to_hass(self) -> None:
        def on_cycle_event(_event: Event) -> None:
            self.schedule_update_ha_state()

        await super().async_added_to_hass()

        self.async_on_remove(self.hass.bus.async_listen(EVENT_SAT_CYCLE_ENDED, on_cycle_event))

    @property
    def name(self) -> str:
        return "Cycle Status"

    @property
    def native_value(self) -> str:
        if self._heating_control is None or self._heating_control.last_cycle is None:
            return CycleClassification.INSUFFICIENT_DATA.name

        return self._heating_control.last_cycle.classification.name

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        if self._heating_control is None:
            return {}

        cycle = self._heating_control.last_cycle
        if cycle is None:
            return {}

        return {
            "kind": cycle.kind.name,
            "sample_count": cycle.sample_count,
            "duration_seconds": round(cycle.duration, 1),
            "max_flow_temperature": cycle.max_flow_temperature,
            "fraction_space_heating": cycle.fraction_space_heating,
            "fraction_domestic_hot_water": cycle.fraction_domestic_hot_water,
            "tail_hot_water_active_fraction": cycle.tail.hot_water_active_fraction,
            "tail_flow_control_setpoint_error_p90": cycle.tail.flow_control_setpoint_error.p90,
            "tail_flow_requested_setpoint_error_p90": cycle.tail.flow_requested_setpoint_error.p90,
            "tail_flow_temperature_p90": cycle.tail.flow_temperature.p90,
            "tail_control_setpoint_p50": cycle.tail.control_setpoint.p50,
            "tail_requested_setpoint_p50": cycle.tail.requested_setpoint.p50,
        }

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-cycle-status"


class SatDeviceSensor(SatEntity, SensorEntity):
    @property
    def name(self) -> str:
        return "Device Status"

    @property
    def native_value(self) -> str:
        if self._heating_control is None:
            return BoilerStatus.INSUFFICIENT_DATA.name

        return self._heating_control.device_status.name

    @property
    def available(self) -> bool:
        if self._heating_control is None:
            return False

        return self._heating_control.device_status != BoilerStatus.INSUFFICIENT_DATA

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-boiler-status"
