"""Sensor platform for SAT."""
import logging
from typing import List, Optional, cast

from homeassistant.components import sensor
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature, PERCENTAGE, UnitOfPressure, UnitOfVolume, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import async_generate_entity_id
from pyotgw.vars import *

from . import TRANSLATE_SOURCE, SatSerialCoordinator
from ..entity import SatEntity
from ..entry_data import SatConfig, get_entry_data

_LOGGER = logging.getLogger(__name__)


class SatSensorInfo:
    def __init__(self, device_class: Optional[str], unit: Optional[str], friendly_name_format: str, status_sources: List[str]):
        self.unit = unit
        self.device_class = device_class
        self.status_sources = status_sources
        self.friendly_name_format = friendly_name_format


SENSOR_INFO: dict[str, SatSensorInfo] = {
    DATA_CONTROL_SETPOINT: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Control Setpoint {}", [BOILER, THERMOSTAT]),
    DATA_MASTER_MEMBERID: SatSensorInfo(None, None, "Thermostat Member ID {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_MEMBERID: SatSensorInfo(None, None, "Boiler Member ID {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_OEM_FAULT: SatSensorInfo(None, None, "Boiler OEM Fault Code {}", [BOILER, THERMOSTAT]),
    DATA_COOLING_CONTROL: SatSensorInfo(None, PERCENTAGE, "Cooling Control Signal {}", [BOILER, THERMOSTAT]),
    DATA_CONTROL_SETPOINT_2: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Control Setpoint 2 {}", [BOILER, THERMOSTAT]),
    DATA_ROOM_SETPOINT_OVRD: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Room Setpoint Override {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_MAX_RELATIVE_MOD: SatSensorInfo(None, PERCENTAGE, "Boiler Maximum Relative Modulation {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_MAX_CAPACITY: SatSensorInfo(sensor.SensorDeviceClass.POWER, UnitOfPower.KILO_WATT, "Boiler Maximum Capacity {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_MIN_MOD_LEVEL: SatSensorInfo(None, PERCENTAGE, "Boiler Minimum Modulation Level {}", [BOILER, THERMOSTAT]),
    DATA_ROOM_SETPOINT: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Room Setpoint {}", [BOILER, THERMOSTAT]),
    DATA_REL_MOD_LEVEL: SatSensorInfo(None, PERCENTAGE, "Relative Modulation Level {}", [BOILER, THERMOSTAT], ),
    DATA_CH_WATER_PRESS: SatSensorInfo(sensor.SensorDeviceClass.PRESSURE, UnitOfPressure.BAR, "Central Heating Water Pressure {}", [BOILER, THERMOSTAT]),
    DATA_DHW_FLOW_RATE: SatSensorInfo(None, f"{UnitOfVolume.LITERS}/{UnitOfTime.MINUTES}", "Hot Water Flow Rate {}", [BOILER, THERMOSTAT]),
    DATA_ROOM_SETPOINT_2: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Room Setpoint 2 {}", [BOILER, THERMOSTAT]),
    DATA_ROOM_TEMP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Room Temperature {}", [BOILER, THERMOSTAT]),
    DATA_CH_WATER_TEMP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Central Heating Water Temperature {}", [BOILER, THERMOSTAT]),
    DATA_DHW_TEMP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Hot Water Temperature {}", [BOILER, THERMOSTAT]),
    DATA_OUTSIDE_TEMP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Outside Temperature {}", [BOILER, THERMOSTAT]),
    DATA_RETURN_WATER_TEMP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Return Water Temperature {}", [BOILER, THERMOSTAT]),
    DATA_SOLAR_STORAGE_TEMP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Solar Storage Temperature {}", [BOILER, THERMOSTAT]),
    DATA_SOLAR_COLL_TEMP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Solar Collector Temperature {}", [BOILER, THERMOSTAT]),
    DATA_CH_WATER_TEMP_2: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Central Heating 2 Water Temperature {}", [BOILER, THERMOSTAT]),
    DATA_DHW_TEMP_2: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Hot Water 2 Temperature {}", [BOILER, THERMOSTAT]),
    DATA_EXHAUST_TEMP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Exhaust Temperature {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_DHW_MAX_SETP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Hot Water Maximum Setpoint {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_DHW_MIN_SETP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Hot Water Minimum Setpoint {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_CH_MAX_SETP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Boiler Maximum Central Heating Setpoint {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_CH_MIN_SETP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Boiler Minimum Central Heating Setpoint {}", [BOILER, THERMOSTAT]),
    DATA_MAX_CH_SETPOINT: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Maximum Central Heating Setpoint {}", [BOILER, THERMOSTAT]),
    DATA_OEM_DIAG: SatSensorInfo(None, None, "OEM Diagnostic Code {}", [BOILER, THERMOSTAT]),
    DATA_TOTAL_BURNER_STARTS: SatSensorInfo(None, None, "Total Burner Starts {}", [BOILER, THERMOSTAT]),
    DATA_CH_PUMP_STARTS: SatSensorInfo(None, None, "Central Heating Pump Starts {}", [BOILER, THERMOSTAT]),
    DATA_DHW_PUMP_STARTS: SatSensorInfo(None, None, "Hot Water Pump Starts {}", [BOILER, THERMOSTAT]),
    DATA_DHW_BURNER_STARTS: SatSensorInfo(None, None, "Hot Water Burner Starts {}", [BOILER, THERMOSTAT]),
    DATA_TOTAL_BURNER_HOURS: SatSensorInfo(sensor.SensorDeviceClass.DURATION, UnitOfTime.HOURS, "Total Burner Hours {}", [BOILER, THERMOSTAT]),
    DATA_CH_PUMP_HOURS: SatSensorInfo(sensor.SensorDeviceClass.DURATION, UnitOfTime.HOURS, "Central Heating Pump Hours {}", [BOILER, THERMOSTAT]),
    DATA_DHW_PUMP_HOURS: SatSensorInfo(sensor.SensorDeviceClass.DURATION, UnitOfTime.HOURS, "Hot Water Pump Hours {}", [BOILER, THERMOSTAT]),
    DATA_DHW_BURNER_HOURS: SatSensorInfo(sensor.SensorDeviceClass.DURATION, UnitOfTime.HOURS, "Hot Water Burner Hours {}", [BOILER, THERMOSTAT]),
    DATA_MASTER_OT_VERSION: SatSensorInfo(None, None, "Thermostat OpenTherm Version {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_OT_VERSION: SatSensorInfo(None, None, "Boiler OpenTherm Version {}", [BOILER, THERMOSTAT]),
    DATA_MASTER_PRODUCT_TYPE: SatSensorInfo(None, None, "Thermostat Product Type {}", [BOILER, THERMOSTAT]),
    DATA_MASTER_PRODUCT_VERSION: SatSensorInfo(None, None, "Thermostat Product Version {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_PRODUCT_TYPE: SatSensorInfo(None, None, "Boiler Product Type {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_PRODUCT_VERSION: SatSensorInfo(None, None, "Boiler Product Version {}", [BOILER, THERMOSTAT]),

    OTGW_MODE: SatSensorInfo(None, None, "Gateway/Monitor Mode {}", [OTGW]),
    OTGW_DHW_OVRD: SatSensorInfo(None, None, "Gateway Hot Water Override Mode {}", [OTGW]),
    OTGW_ABOUT: SatSensorInfo(None, None, "Gateway Firmware Version {}", [OTGW]),
    OTGW_BUILD: SatSensorInfo(None, None, "Gateway Firmware Build {}", [OTGW]),
    OTGW_SB_TEMP: SatSensorInfo(sensor.SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, "Gateway Setback Temperature {}", [OTGW]),
    OTGW_SETP_OVRD_MODE: SatSensorInfo(None, None, "Gateway Room Setpoint Override Mode {}", [OTGW]),
    OTGW_SMART_PWR: SatSensorInfo(None, None, "Gateway Smart Power Mode {}", [OTGW]),
    OTGW_THRM_DETECT: SatSensorInfo(None, None, "Gateway Thermostat Detection {}", [OTGW]),
    OTGW_VREF: SatSensorInfo(None, None, "Gateway Reference Voltage Setting {}", [OTGW]),
}


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities) -> None:
    """Setup sensor platform."""
    entry_data = get_entry_data(hass, config_entry.entry_id)
    coordinator = cast(SatSerialCoordinator, entry_data.coordinator)
    has_thermostat = coordinator.data[OTGW].get(OTGW_THRM_DETECT) != "D"

    # Create a list of entities to be added
    entities = []

    # Iterate through sensor information
    for key, info in SENSOR_INFO.items():
        # Check if the sensor should be added based on its availability and thermostat presence
        for source in info.status_sources:
            if source == THERMOSTAT and has_thermostat is False:
                continue

            if coordinator.data[source].get(key) is not None:
                entities.append(SatSensor(coordinator, entry_data.config, info, key, source))

    # Add all devices
    async_add_entities(entities)


class SatSensor(SatEntity, sensor.SensorEntity):
    def __init__(self, coordinator: SatSerialCoordinator, config: SatConfig, info: SatSensorInfo, key: str, source: str):
        super().__init__(coordinator, config)

        self.entity_id = async_generate_entity_id(
            sensor.DOMAIN + ".{}", f"{self._config.name_lower}_{source}_{key}", hass=coordinator.hass
        )

        self._key = key
        self._unit = info.unit
        self._source = source
        self._device_class = info.device_class

        friendly_name_format = info.friendly_name_format
        if TRANSLATE_SOURCE[source] is not None:
            friendly_name_format = f"{friendly_name_format} ({TRANSLATE_SOURCE[source]})"

        self._friendly_name = friendly_name_format.format(self._config.name)

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return self._friendly_name

    @property
    def device_class(self):
        """Return the device class."""
        return self._device_class

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.data[self._source].get(self._key) is not None

    @property
    def native_value(self):
        """Return the state of the device."""
        value = self._coordinator.data[self._source].get(self._key)
        if isinstance(value, float):
            value = f"{value:2.1f}"

        return value

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config.name_lower}-{self._source}-{self._key}"
