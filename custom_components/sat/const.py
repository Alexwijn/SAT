import pyotgw.vars as gw_vars
from homeassistant.backports.enum import StrEnum
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    UnitOfTemperature,
    UnitOfPressure,
    UnitOfVolume,
    UnitOfPower,
    TIME_MINUTES,
    PERCENTAGE
)

# Base component constants
NAME = "Smart Autotune Thermostat"
DOMAIN = "sat"
VERSION = "0.0.1"
COORDINATOR = "coordinator"

UNIT_KW = "kW"
UNIT_L_MIN = f"L/{TIME_MINUTES}"

# Icons
ICON = "mdi:format-quote-close"

# Device classes
BINARY_SENSOR_DEVICE_CLASS = "connectivity"

# Platforms
SENSOR = "sensor"
BINARY_SENSOR = "binary_sensor"
CLIMATE = "climate"

# Configuration and options
CONF_NAME = "name"
CONF_DEVICE = "device"
CONF_CLIMATES = "climates"
CONF_MAIN_CLIMATES = "main_climates"
CONF_SIMULATION = "simulation"
CONF_INTEGRAL = "integral"
CONF_DERIVATIVE = "derivative"
CONF_PROPORTIONAL = "proportional"
CONF_SAMPLE_TIME = "sample_time"
CONF_MIN_NUM_UPDATES = "min_num_updates"
CONF_CLIMATE_VALVE_OFFSET = "climate_valve_offset"
CONF_SENSOR_MAX_VALUE_AGE = "sensor_max_value_age"
CONF_OVERSHOOT_PROTECTION = "overshoot_protection"
CONF_TARGET_TEMPERATURE_STEP = "target_temperature_step"
CONF_INSIDE_SENSOR_ENTITY_ID = "inside_sensor_entity_id"
CONF_OUTSIDE_SENSOR_ENTITY_ID = "outside_sensor_entity_id"

CONF_HEATING_SYSTEM = "heating_system"
CONF_HEATING_CURVE_COEFFICIENT = "heating_curve_coefficient"

CONF_AWAY_TEMPERATURE = "away_temperature"
CONF_HOME_TEMPERATURE = "home_temperature"
CONF_SLEEP_TEMPERATURE = "sleep_temperature"
CONF_COMFORT_TEMPERATURE = "comfort_temperature"

HEATING_SYSTEM_UNDERFLOOR = "underfloor"
HEATING_SYSTEM_RADIATOR_LOW_TEMPERATURES = "radiator_low_temperatures"
HEATING_SYSTEM_RADIATOR_HIGH_TEMPERATURES = "radiator_high_temperatures"

OPTIONS_DEFAULTS = {
    CONF_PROPORTIONAL: "45",
    CONF_INTEGRAL: "0",
    CONF_DERIVATIVE: "6000",

    CONF_CLIMATES: [],
    CONF_MAIN_CLIMATES: [],
    CONF_SIMULATION: False,
    CONF_MIN_NUM_UPDATES: 20,
    CONF_SAMPLE_TIME: "00:00:00",
    CONF_CLIMATE_VALVE_OFFSET: 0,
    CONF_OVERSHOOT_PROTECTION: False,
    CONF_TARGET_TEMPERATURE_STEP: 0.5,
    CONF_SENSOR_MAX_VALUE_AGE: "06:00:00",

    CONF_AWAY_TEMPERATURE: 10,
    CONF_HOME_TEMPERATURE: 18,
    CONF_SLEEP_TEMPERATURE: 15,
    CONF_COMFORT_TEMPERATURE: 20,

    CONF_HEATING_CURVE_COEFFICIENT: 1.0,
    CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATOR_LOW_TEMPERATURES,
}

# Storage
STORAGE_OVERSHOOT_PROTECTION_VALUE = "overshoot_protection_value"

# Config steps
STEP_SETUP_GATEWAY = "gateway"
STEP_SETUP_SENSORS = "sensors"

# Defaults
DEFAULT_NAME = DOMAIN

# Sensors
TRANSLATE_SOURCE = {
    gw_vars.OTGW: None,
    gw_vars.BOILER: "Boiler",
    gw_vars.THERMOSTAT: "Thermostat",
}


# Time units
class UnitOfTime(StrEnum):
    """Time units."""

    MICROSECONDS = "μs"
    MILLISECONDS = "ms"
    SECONDS = "s"
    MINUTES = "min"
    HOURS = "h"
    DAYS = "d"
    WEEKS = "w"
    MONTHS = "m"
    YEARS = "y"


BINARY_SENSOR_INFO: dict[str, list] = {
    # [device_class, friendly_name format, [status source, ...]]
    gw_vars.DATA_MASTER_CH_ENABLED: [
        None,
        "Thermostat Central Heating {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_MASTER_DHW_ENABLED: [
        None,
        "Thermostat Hot Water {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_MASTER_COOLING_ENABLED: [
        None,
        "Thermostat Cooling {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_MASTER_OTC_ENABLED: [
        None,
        "Thermostat Outside Temperature Correction {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_MASTER_CH2_ENABLED: [
        None,
        "Thermostat Central Heating 2 {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_FAULT_IND: [
        BinarySensorDeviceClass.PROBLEM,
        "Boiler Fault {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_CH_ACTIVE: [
        BinarySensorDeviceClass.HEAT,
        "Boiler Central Heating {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_DHW_ACTIVE: [
        BinarySensorDeviceClass.HEAT,
        "Boiler Hot Water {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_FLAME_ON: [
        BinarySensorDeviceClass.HEAT,
        "Boiler Flame {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_COOLING_ACTIVE: [
        BinarySensorDeviceClass.COLD,
        "Boiler Cooling {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_CH2_ACTIVE: [
        BinarySensorDeviceClass.HEAT,
        "Boiler Central Heating 2 {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_DIAG_IND: [
        BinarySensorDeviceClass.PROBLEM,
        "Boiler Diagnostics {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_DHW_PRESENT: [
        None,
        "Boiler Hot Water Present {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_CONTROL_TYPE: [
        None,
        "Boiler Control Type {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_COOLING_SUPPORTED: [
        None,
        "Boiler Cooling Support {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_DHW_CONFIG: [
        None,
        "Boiler Hot Water Configuration {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_MASTER_LOW_OFF_PUMP: [
        None,
        "Boiler Pump Commands Support {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_CH2_PRESENT: [
        None,
        "Boiler Central Heating 2 Present {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_SERVICE_REQ: [
        BinarySensorDeviceClass.PROBLEM,
        "Boiler Service Required {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_REMOTE_RESET: [
        None,
        "Boiler Remote Reset Support {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_LOW_WATER_PRESS: [
        BinarySensorDeviceClass.PROBLEM,
        "Boiler Low Water Pressure {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_GAS_FAULT: [
        BinarySensorDeviceClass.PROBLEM,
        "Boiler Gas Fault {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_AIR_PRESS_FAULT: [
        BinarySensorDeviceClass.PROBLEM,
        "Boiler Air Pressure Fault {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_WATER_OVERTEMP: [
        BinarySensorDeviceClass.PROBLEM,
        "Boiler Water Over-temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_REMOTE_TRANSFER_DHW: [
        None,
        "Remote Hot Water Setpoint Transfer Support {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_REMOTE_TRANSFER_MAX_CH: [
        None,
        "Remote Maximum Central Heating Setpoint Write Support {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_REMOTE_RW_DHW: [
        None,
        "Remote Hot Water Setpoint Write Support {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_REMOTE_RW_MAX_CH: [
        None,
        "Remote Central Heating Setpoint Write Support {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_ROVRD_MAN_PRIO: [
        None,
        "Remote Override Manual Change Priority {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_ROVRD_AUTO_PRIO: [
        None,
        "Remote Override Program Change Priority {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.OTGW_GPIO_A_STATE: [
        None,
        "Gateway GPIO A {}",
        [gw_vars.OTGW]
    ],
    gw_vars.OTGW_GPIO_B_STATE: [
        None,
        "Gateway GPIO B {}",
        [gw_vars.OTGW]
    ],
    gw_vars.OTGW_IGNORE_TRANSITIONS: [
        None,
        "Gateway Ignore Transitions {}",
        [gw_vars.OTGW],
    ],
    gw_vars.OTGW_OVRD_HB: [
        None,
        "Gateway Override High Byte {}",
        [gw_vars.OTGW]
    ],
}

SENSOR_INFO: dict[str, list] = {
    # [device_class, unit, friendly_name, [status source, ...]]
    gw_vars.DATA_CONTROL_SETPOINT: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Control Setpoint {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_MASTER_MEMBERID: [
        None,
        None,
        "Thermostat Member ID {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_MEMBERID: [
        None,
        None,
        "Boiler Member ID {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_OEM_FAULT: [
        None,
        None,
        "Boiler OEM Fault Code {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_COOLING_CONTROL: [
        None,
        PERCENTAGE,
        "Cooling Control Signal {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_CONTROL_SETPOINT_2: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Control Setpoint 2 {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_ROOM_SETPOINT_OVRD: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Room Setpoint Override {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_MAX_RELATIVE_MOD: [
        None,
        PERCENTAGE,
        "Boiler Maximum Relative Modulation {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_MAX_CAPACITY: [
        SensorDeviceClass.POWER,
        UnitOfPower.KILO_WATT,
        "Boiler Maximum Capacity {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_MIN_MOD_LEVEL: [
        None,
        PERCENTAGE,
        "Boiler Minimum Modulation Level {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_ROOM_SETPOINT: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Room Setpoint {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_REL_MOD_LEVEL: [
        None,
        PERCENTAGE,
        "Relative Modulation Level {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_CH_WATER_PRESS: [
        SensorDeviceClass.PRESSURE,
        UnitOfPressure.BAR,
        "Central Heating Water Pressure {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_DHW_FLOW_RATE: [
        None,
        f"{UnitOfVolume.LITERS}/{UnitOfTime.MINUTES}",
        "Hot Water Flow Rate {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_ROOM_SETPOINT_2: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Room Setpoint 2 {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_ROOM_TEMP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Room Temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_CH_WATER_TEMP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Central Heating Water Temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_DHW_TEMP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Hot Water Temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_OUTSIDE_TEMP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Outside Temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_RETURN_WATER_TEMP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Return Water Temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SOLAR_STORAGE_TEMP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Solar Storage Temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SOLAR_COLL_TEMP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Solar Collector Temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_CH_WATER_TEMP_2: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Central Heating 2 Water Temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_DHW_TEMP_2: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Hot Water 2 Temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_EXHAUST_TEMP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Exhaust Temperature {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_DHW_MAX_SETP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Hot Water Maximum Setpoint {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_DHW_MIN_SETP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Hot Water Minimum Setpoint {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_CH_MAX_SETP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Boiler Maximum Central Heating Setpoint {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_CH_MIN_SETP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Boiler Minimum Central Heating Setpoint {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_DHW_SETPOINT: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Hot Water Setpoint {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_MAX_CH_SETPOINT: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Maximum Central Heating Setpoint {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_OEM_DIAG: [
        None,
        None,
        "OEM Diagnostic Code {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_TOTAL_BURNER_STARTS: [
        None,
        None,
        "Total Burner Starts {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_CH_PUMP_STARTS: [
        None,
        None,
        "Central Heating Pump Starts {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_DHW_PUMP_STARTS: [
        None,
        None,
        "Hot Water Pump Starts {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_DHW_BURNER_STARTS: [
        None,
        None,
        "Hot Water Burner Starts {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_TOTAL_BURNER_HOURS: [
        SensorDeviceClass.DURATION,
        UnitOfTime.HOURS,
        "Total Burner Hours {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_CH_PUMP_HOURS: [
        SensorDeviceClass.DURATION,
        UnitOfTime.HOURS,
        "Central Heating Pump Hours {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_DHW_PUMP_HOURS: [
        SensorDeviceClass.DURATION,
        UnitOfTime.HOURS,
        "Hot Water Pump Hours {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_DHW_BURNER_HOURS: [
        SensorDeviceClass.DURATION,
        UnitOfTime.HOURS,
        "Hot Water Burner Hours {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_MASTER_OT_VERSION: [
        None,
        None,
        "Thermostat OpenTherm Version {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_OT_VERSION: [
        None,
        None,
        "Boiler OpenTherm Version {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_MASTER_PRODUCT_TYPE: [
        None,
        None,
        "Thermostat Product Type {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_MASTER_PRODUCT_VERSION: [
        None,
        None,
        "Thermostat Product Version {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_PRODUCT_TYPE: [
        None,
        None,
        "Boiler Product Type {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.DATA_SLAVE_PRODUCT_VERSION: [
        None,
        None,
        "Boiler Product Version {}",
        [gw_vars.BOILER, gw_vars.THERMOSTAT],
    ],
    gw_vars.OTGW_MODE: [
        None,
        None,
        "Gateway/Monitor Mode {}",
        [gw_vars.OTGW]
    ],
    gw_vars.OTGW_DHW_OVRD: [
        None,
        None,
        "Gateway Hot Water Override Mode {}",
        [gw_vars.OTGW],
    ],
    gw_vars.OTGW_ABOUT: [
        None,
        None,
        "Gateway Firmware Version {}",
        [gw_vars.OTGW]
    ],
    gw_vars.OTGW_BUILD: [
        None,
        None,
        "Gateway Firmware Build {}",
        [gw_vars.OTGW]
    ],
    gw_vars.OTGW_SB_TEMP: [
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        "Gateway Setback Temperature {}",
        [gw_vars.OTGW],
    ],
    gw_vars.OTGW_SETP_OVRD_MODE: [
        None,
        None,
        "Gateway Room Setpoint Override Mode {}",
        [gw_vars.OTGW],
    ],
    gw_vars.OTGW_SMART_PWR: [
        None,
        None,
        "Gateway Smart Power Mode {}",
        [gw_vars.OTGW]
    ],
    gw_vars.OTGW_THRM_DETECT: [
        None,
        None,
        "Gateway Thermostat Detection {}",
        [gw_vars.OTGW],
    ],
    gw_vars.OTGW_VREF: [
        None,
        None,
        "Gateway Reference Voltage Setting {}",
        [gw_vars.OTGW],
    ],
}
