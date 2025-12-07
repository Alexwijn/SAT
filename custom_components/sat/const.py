# Base component constants
from __future__ import annotations

from enum import Enum

NAME = "Smart Autotune Thermostat"
DOMAIN = "sat"
CLIMATE = "climate"
SENTRY = "sentry"
COORDINATOR = "coordinator"
CONFIG_STORE = "config_store"

MODE_FAKE = "fake"
MODE_MQTT_EMS = "mqtt_ems"
MODE_MQTT_OPENTHERM = "mqtt_opentherm"
MODE_SWITCH = "switch"
MODE_SERIAL = "serial"
MODE_ESPHOME = "esphome"
MODE_SIMULATOR = "simulator"

DEADBAND = 0.1
BOILER_DEADBAND = 2
HEATER_STARTUP_TIMEFRAME = 180
PWM_ENABLE_MARGIN_CELSIUS = 0.5
PWM_DISABLE_MARGIN_CELSIUS = 1.5

COLD_SETPOINT = 28.2
MINIMUM_SETPOINT = 10
MAXIMUM_SETPOINT = 65
MINIMUM_RELATIVE_MODULATION = 0
MAXIMUM_RELATIVE_MODULATION = 100

MAX_BOILER_TEMPERATURE_AGE = 60

# Configuration and options
CONF_MODE = "mode"
CONF_NAME = "name"
CONF_DEVICE = "device"
CONF_THERMOSTAT = "thermostat"
CONF_MANUFACTURER = "manufacturer"
CONF_ERROR_MONITORING = "error_monitoring"
CONF_CYCLES_PER_HOUR = "cycles_per_hour"
CONF_SIMULATED_HEATING = "simulated_heating"
CONF_SIMULATED_COOLING = "simulated_cooling"
CONF_SIMULATED_WARMING_UP = "simulated_warming_up"
CONF_MINIMUM_SETPOINT = "minimum_setpoint"
CONF_MAXIMUM_SETPOINT = "maximum_setpoint"
CONF_MAXIMUM_RELATIVE_MODULATION = "maximum_relative_modulation"
CONF_ROOMS = "secondary_climates"
CONF_MQTT_TOPIC = "mqtt_topic"
CONF_RADIATORS = "main_climates"
CONF_WINDOW_SENSORS = "window_sensors"
CONF_PUSH_SETPOINT_TO_THERMOSTAT = "push_setpoint_to_thermostat"
CONF_WINDOW_MINIMUM_OPEN_TIME = "window_minimum_open_time"
CONF_THERMAL_COMFORT = "thermal_comfort"
CONF_SIMULATION = "simulation"
CONF_INTEGRAL = "integral"
CONF_DERIVATIVE = "derivative"
CONF_PROPORTIONAL = "proportional"
CONF_DUTY_CYCLE = "duty_cycle"
CONF_SAMPLE_TIME = "sample_time"
CONF_AUTOMATIC_GAINS = "automatic_gains"
CONF_AUTOMATIC_DUTY_CYCLE = "automatic_duty_cycle"
CONF_AUTOMATIC_GAINS_VALUE = "automatic_gains_value"
CONF_DERIVATIVE_TIME_WEIGHT = "derivative_time_weight"
CONF_CLIMATE_VALVE_OFFSET = "climate_valve_offset"
CONF_SENSOR_MAX_VALUE_AGE = "sensor_max_value_age"
CONF_OVERSHOOT_PROTECTION = "overshoot_protection"
CONF_SYNC_CLIMATES_WITH_MODE = "sync_climates_with_mode"
CONF_SYNC_CLIMATES_WITH_PRESET = "sync_climates_with_preset"
CONF_FORCE_PULSE_WIDTH_MODULATION = "force_pulse_width_modulation"
CONF_TARGET_TEMPERATURE_STEP = "target_temperature_step"
CONF_INSIDE_SENSOR_ENTITY_ID = "inside_sensor_entity_id"
CONF_OUTSIDE_SENSOR_ENTITY_ID = "outside_sensor_entity_id"
CONF_HUMIDITY_SENSOR_ENTITY_ID = "humidity_sensor_entity_id"

CONF_HEATING_MODE = "heating_mode"
CONF_HEATING_SYSTEM = "heating_system"
CONF_HEATING_CURVE_COEFFICIENT = "heating_curve_coefficient"

CONF_DYNAMIC_MINIMUM_SETPOINT = "dynamic_minimum_setpoint"
CONF_MINIMUM_SETPOINT_ADJUSTMENT_FACTOR = "minimum_setpoint_adjustment_factor"

CONF_MINIMUM_CONSUMPTION = "minimum_consumption"
CONF_MAXIMUM_CONSUMPTION = "maximum_consumption"

CONF_AWAY_TEMPERATURE = "away_temperature"
CONF_HOME_TEMPERATURE = "home_temperature"
CONF_SLEEP_TEMPERATURE = "sleep_temperature"
CONF_COMFORT_TEMPERATURE = "comfort_temperature"
CONF_ACTIVITY_TEMPERATURE = "activity_temperature"

HEATING_SYSTEM_UNKNOWN = "unknown"
HEATING_SYSTEM_HEAT_PUMP = "heat_pump"
HEATING_SYSTEM_RADIATORS = "radiators"
HEATING_SYSTEM_UNDERFLOOR = "underfloor"

HEATING_MODE_ECO = "eco"
HEATING_MODE_COMFORT = "comfort"

OPTIONS_DEFAULTS = {
    CONF_PROPORTIONAL: "45",
    CONF_INTEGRAL: "0",
    CONF_DERIVATIVE: "6000",
    CONF_ERROR_MONITORING: False,

    CONF_CYCLES_PER_HOUR: 4,
    CONF_AUTOMATIC_GAINS: True,
    CONF_AUTOMATIC_DUTY_CYCLE: True,
    CONF_AUTOMATIC_GAINS_VALUE: 2.0,
    CONF_DERIVATIVE_TIME_WEIGHT: 2.5,
    CONF_OVERSHOOT_PROTECTION: False,
    CONF_DYNAMIC_MINIMUM_SETPOINT: False,
    CONF_MINIMUM_SETPOINT_ADJUSTMENT_FACTOR: 0.2,

    CONF_RADIATORS: [],
    CONF_ROOMS: [],

    CONF_SIMULATION: False,
    CONF_WINDOW_SENSORS: [],
    CONF_THERMAL_COMFORT: False,
    CONF_HUMIDITY_SENSOR_ENTITY_ID: None,
    CONF_PUSH_SETPOINT_TO_THERMOSTAT: False,
    CONF_SYNC_CLIMATES_WITH_MODE: True,
    CONF_SYNC_CLIMATES_WITH_PRESET: False,

    CONF_SIMULATED_HEATING: 20,
    CONF_SIMULATED_COOLING: 5,

    CONF_MINIMUM_SETPOINT: 10,
    CONF_MAXIMUM_RELATIVE_MODULATION: 100,
    CONF_FORCE_PULSE_WIDTH_MODULATION: False,

    CONF_MINIMUM_CONSUMPTION: 0,
    CONF_MAXIMUM_CONSUMPTION: 0,

    CONF_DUTY_CYCLE: "00:13:00",
    CONF_SAMPLE_TIME: "00:00:30",
    CONF_CLIMATE_VALVE_OFFSET: 0,
    CONF_TARGET_TEMPERATURE_STEP: 0.5,
    CONF_SENSOR_MAX_VALUE_AGE: "06:00:00",
    CONF_SIMULATED_WARMING_UP: "00:00:15",
    CONF_WINDOW_MINIMUM_OPEN_TIME: "00:00:15",

    CONF_ACTIVITY_TEMPERATURE: 10,
    CONF_AWAY_TEMPERATURE: 10,
    CONF_HOME_TEMPERATURE: 18,
    CONF_SLEEP_TEMPERATURE: 15,
    CONF_COMFORT_TEMPERATURE: 20,

    CONF_HEATING_CURVE_COEFFICIENT: 2.0,
    CONF_HEATING_MODE: HEATING_MODE_COMFORT,
    CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
}

# Overshoot protection
OVERSHOOT_PROTECTION_REQUIRED_DATASET = 40
OVERSHOOT_PROTECTION_SETPOINT = {
    HEATING_SYSTEM_HEAT_PUMP: 40,
    HEATING_SYSTEM_RADIATORS: 62,
    HEATING_SYSTEM_UNDERFLOOR: 45,
}

# Storage
STORAGE_OVERSHOOT_PROTECTION_VALUE = "overshoot_protection_value"

# Services
SERVICE_RESET_INTEGRAL = "reset_integral"
SERVICE_PULSE_WIDTH_MODULATION = "pulse_width_modulation"
SERVICE_SET_OVERSHOOT_PROTECTION_VALUE = "set_overshoot_protection_value"
SERVICE_START_OVERSHOOT_PROTECTION_CALCULATION = "start_overshoot_protection_calculation"

# Config steps
STEP_SETUP_GATEWAY = "gateway"
STEP_SETUP_SENSORS = "sensors"

# Events
EVENT_SAT_CYCLE_STARTED = "sat_cycle_started"
EVENT_SAT_CYCLE_ENDED = "sat_cycle_ended"


# Enumerations
class CycleKind(str, Enum):
    MIXED = "mixed"
    UNKNOWN = "unknown"
    CENTRAL_HEATING = "central_heating"
    DOMESTIC_HOT_WATER = "domestic_hot_water"


class CycleClassification(str, Enum):
    GOOD = "good"
    UNCERTAIN = "uncertain"
    LONG_UNDERHEAT = "long_underheat"
    INSUFFICIENT_DATA = "insufficient_data"
    TOO_SHORT_UNDERHEAT = "too_short_underheat"
    TOO_SHORT_OVERSHOOT = "too_short_overshoot"
    SHORT_CYCLING_OVERSHOOT = "short_cycling_overshoot"


class BoilerStatus(Enum):
    OFF = "off"
    IDLE = "idle"
    INSUFFICIENT_DATA = "insufficient_data"

    PREHEATING = "preheating"
    AT_SETPOINT_BAND = "at_setpoint_band"
    STALLED_IGNITION = "stalled_ignition"

    MODULATING_UP = "modulating_up"
    MODULATING_DOWN = "modulating_down"
    CENTRAL_HEATING = "central_heating"
    HEATING_HOT_WATER = "heating_hot_water"

    COOLING = "cooling"
    ANTI_CYCLING = "anti_cycling"
    PUMP_STARTING = "pump_starting"
    SHORT_CYCLING = "short_cycling"
    WAITING_FOR_FLAME = "waiting_for_flame"
    OVERSHOOT_COOLING = "overshoot_cooling"
    POST_CYCLE_SETTLING = "post_cycle_settling"


class PWMStatus(str, Enum):
    ON = "on"
    OFF = "off"
    IDLE = "idle"


class RelativeModulationState(str, Enum):
    OFF = "off"
    COLD = "cold"
    PWM_OFF = "pwm_off"
    HOT_WATER = "hot_water"


# Cycles
UNHEALTHY_CYCLES = (
    CycleClassification.TOO_SHORT_UNDERHEAT,
    CycleClassification.TOO_SHORT_OVERSHOOT,
    CycleClassification.SHORT_CYCLING_OVERSHOOT
)
