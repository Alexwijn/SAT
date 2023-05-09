from pyotgw.vars import *

# Base component constants
NAME = "Smart Autotune Thermostat"
DOMAIN = "sat"
VERSION = "2.0.2"
COORDINATOR = "coordinator"
CONFIG_STORE = "config_store"

MODE_SWITCH = "switch"
MODE_OPENTHERM = "opentherm"

HOT_TOLERANCE = 0.3
COLD_TOLERANCE = 0.1
MINIMUM_SETPOINT = 10
MINIMUM_RELATIVE_MOD = 0
MAXIMUM_RELATIVE_MOD = 100

OVERSHOOT_PROTECTION_SETPOINT = 75
OVERSHOOT_PROTECTION_MAX_RELATIVE_MOD = 0
OVERSHOOT_PROTECTION_REQUIRED_DATASET = 40

# Icons
ICON = "mdi:format-quote-close"

# Device classes
BINARY_SENSOR_DEVICE_CLASS = "connectivity"

# Platforms
SENSOR = "sensor"
NUMBER = "number"
CLIMATE = "climate"
BINARY_SENSOR = "binary_sensor"

# Configuration and options
CONF_MODE = "mode"
CONF_NAME = "name"
CONF_DEVICE = "device"
CONF_SWITCH = "switch"
CONF_SETPOINT = "setpoint"
CONF_CLIMATES = "climates"
CONF_MAIN_CLIMATES = "main_climates"
CONF_WINDOW_SENSOR = "window_sensor"
CONF_WINDOW_MINIMUM_OPEN_TIME = "window_minimum_open_time"
CONF_SIMULATION = "simulation"
CONF_INTEGRAL = "integral"
CONF_DERIVATIVE = "derivative"
CONF_PROPORTIONAL = "proportional"
CONF_DUTY_CYCLE = "duty_cycle"
CONF_SAMPLE_TIME = "sample_time"
CONF_AUTOMATIC_GAINS = "automatic_gains"
CONF_AUTOMATIC_DUTY_CYCLE = "automatic_duty_cycle"
CONF_CLIMATE_VALVE_OFFSET = "climate_valve_offset"
CONF_SENSOR_MAX_VALUE_AGE = "sensor_max_value_age"
CONF_OVERSHOOT_PROTECTION = "overshoot_protection"
CONF_SYNC_CLIMATES_WITH_PRESET = "sync_climates_with_preset"
CONF_FORCE_PULSE_WIDTH_MODULATION = "force_pulse_width_modulation"
CONF_TARGET_TEMPERATURE_STEP = "target_temperature_step"
CONF_INSIDE_SENSOR_ENTITY_ID = "inside_sensor_entity_id"
CONF_OUTSIDE_SENSOR_ENTITY_ID = "outside_sensor_entity_id"

CONF_HEATING_SYSTEM = "heating_system"
CONF_HEATING_CURVE_COEFFICIENT = "heating_curve_coefficient"

CONF_AWAY_TEMPERATURE = "away_temperature"
CONF_HOME_TEMPERATURE = "home_temperature"
CONF_SLEEP_TEMPERATURE = "sleep_temperature"
CONF_COMFORT_TEMPERATURE = "comfort_temperature"
CONF_ACTIVITY_TEMPERATURE = "activity_temperature"

HEATING_SYSTEM_UNDERFLOOR = "underfloor"
HEATING_SYSTEM_RADIATOR_LOW_TEMPERATURES = "radiator_low_temperatures"
HEATING_SYSTEM_RADIATOR_MEDIUM_TEMPERATURES = "radiator_medium_temperatures"
HEATING_SYSTEM_RADIATOR_HIGH_TEMPERATURES = "radiator_high_temperatures"

OPTIONS_DEFAULTS = {
    CONF_MODE: MODE_OPENTHERM,
    CONF_PROPORTIONAL: "45",
    CONF_INTEGRAL: "0",
    CONF_DERIVATIVE: "6000",

    CONF_CLIMATES: [],
    CONF_MAIN_CLIMATES: [],
    CONF_SIMULATION: False,
    CONF_WINDOW_SENSOR: None,
    CONF_AUTOMATIC_GAINS: False,
    CONF_AUTOMATIC_DUTY_CYCLE: False,
    CONF_SYNC_CLIMATES_WITH_PRESET: False,

    CONF_SETPOINT: 80,
    CONF_OVERSHOOT_PROTECTION: False,
    CONF_FORCE_PULSE_WIDTH_MODULATION: False,

    CONF_DUTY_CYCLE: "00:13:00",
    CONF_SAMPLE_TIME: "00:01:00",
    CONF_CLIMATE_VALVE_OFFSET: 0,
    CONF_TARGET_TEMPERATURE_STEP: 0.5,
    CONF_SENSOR_MAX_VALUE_AGE: "06:00:00",
    CONF_WINDOW_MINIMUM_OPEN_TIME: "00:00:15",

    CONF_ACTIVITY_TEMPERATURE: 10,
    CONF_AWAY_TEMPERATURE: 10,
    CONF_HOME_TEMPERATURE: 18,
    CONF_SLEEP_TEMPERATURE: 15,
    CONF_COMFORT_TEMPERATURE: 20,

    CONF_HEATING_CURVE_COEFFICIENT: 1.0,
    CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATOR_LOW_TEMPERATURES,
}

# Storage
STORAGE_OVERSHOOT_PROTECTION_VALUE = "overshoot_protection_value"

# Services
SERVICE_RESET_INTEGRAL = "reset_integral"
SERVICE_SET_OVERSHOOT_PROTECTION_VALUE = "set_overshoot_protection_value"
SERVICE_OVERSHOOT_PROTECTION_CALCULATION = "overshoot_protection_calculation"

# Config steps
STEP_SETUP_GATEWAY = "gateway"
STEP_SETUP_SENSORS = "sensors"

# Defaults
DEFAULT_NAME = DOMAIN

# Sensors
TRANSLATE_SOURCE = {
    OTGW: None,
    BOILER: "Boiler",
    THERMOSTAT: "Thermostat",
}
