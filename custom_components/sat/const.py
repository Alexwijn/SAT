# Core domain identifiers and shared defaults used across the integration.
from __future__ import annotations

from enum import StrEnum

from .types import CycleClassification

NAME = "Smart Autotune Thermostat"
DOMAIN = "sat"
CLIMATE = "climate"
SENTRY = "sentry"
COORDINATOR = "coordinator"
CONFIG_STORE = "config_store"

# Integration operation modes and backends.

# Control loop tolerances and timing thresholds.
DEADBAND = 0.1
BOILER_DEADBAND = 2
FLAME_STARTUP_TIMEFRAME = 30
HEATER_STARTUP_TIMEFRAME = 180
PWM_ENABLE_MARGIN_CELSIUS = 0.5
PWM_DISABLE_MARGIN_CELSIUS = 1.5
PWM_ENABLE_LOW_MODULATION_PERCENT = 10
PWM_DISABLE_LOW_MODULATION_PERCENT = 30
PWM_LOW_MODULATION_PERSISTENCE_TICKS = 6

# Boiler temperature and modulation bounds.
COLD_SETPOINT = 28.2
MINIMUM_SETPOINT = 10.0
MAXIMUM_SETPOINT = 65.0
MINIMUM_RELATIVE_MODULATION = 0
MAXIMUM_RELATIVE_MODULATION = 100

MAX_BOILER_TEMPERATURE_AGE = 60

# Config entry keys and options used by the integration.
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
CONF_ROOM_WEIGHTS = "secondary_climate_weights"
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
CONF_AUTOMATIC_GAINS = "automatic_gains"
CONF_AUTOMATIC_GAINS_VALUE = "automatic_gains_value"
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
CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS = "flame_off_setpoint_offset_celsius"
CONF_MODULATION_SUPPRESSION_DELAY_SECONDS = "modulation_suppression_delay_seconds"
CONF_MODULATION_SUPPRESSION_OFFSET_CELSIUS = "modulation_suppression_offset_celsius"

# Heating system configuration keys.
CONF_HEATING_MODE = "heating_mode"
CONF_HEATING_SYSTEM = "heating_system"
CONF_HEATING_CURVE_COEFFICIENT = "heating_curve_coefficient"

# Dynamic minimum setpoint tuning keys.
CONF_DYNAMIC_MINIMUM_SETPOINT = "dynamic_minimum_setpoint"

# Consumption bounds for energy/cost tracking.
CONF_MINIMUM_CONSUMPTION = "minimum_consumption"
CONF_MAXIMUM_CONSUMPTION = "maximum_consumption"

# Preset temperatures for modes like home/away/sleep.
CONF_AWAY_TEMPERATURE = "away_temperature"
CONF_HOME_TEMPERATURE = "home_temperature"
CONF_SLEEP_TEMPERATURE = "sleep_temperature"
CONF_COMFORT_TEMPERATURE = "comfort_temperature"
CONF_ACTIVITY_TEMPERATURE = "activity_temperature"


class HeatingSystem(StrEnum):
    UNKNOWN = "unknown"
    HEAT_PUMP = "heat_pump"
    RADIATORS = "radiators"
    UNDERFLOOR = "underfloor"


class HeatingMode(StrEnum):
    ECO = "eco"
    COMFORT = "comfort"


# Default values for integration options.
OPTIONS_DEFAULTS = {
    # PID tuning and core control behavior.
    CONF_PROPORTIONAL: "45",
    CONF_INTEGRAL: "0",
    CONF_DERIVATIVE: "6000",
    CONF_ERROR_MONITORING: False,

    # Cycle limits and automatic tuning.
    CONF_CYCLES_PER_HOUR: 4,
    CONF_AUTOMATIC_GAINS: True,
    CONF_AUTOMATIC_GAINS_VALUE: 2.0,
    CONF_OVERSHOOT_PROTECTION: False,
    CONF_DYNAMIC_MINIMUM_SETPOINT: False,

    # Linked climates and weighting.
    CONF_RADIATORS: [],
    CONF_ROOMS: [],
    CONF_ROOM_WEIGHTS: {},

    # General behavior flags and sensors.
    CONF_SIMULATION: False,
    CONF_WINDOW_SENSORS: [],
    CONF_THERMAL_COMFORT: False,
    CONF_HUMIDITY_SENSOR_ENTITY_ID: None,
    CONF_PUSH_SETPOINT_TO_THERMOSTAT: False,
    CONF_SYNC_CLIMATES_WITH_MODE: True,
    CONF_SYNC_CLIMATES_WITH_PRESET: False,

    # Simulation parameters.
    CONF_SIMULATED_HEATING: 20,
    CONF_SIMULATED_COOLING: 5,

    # Setpoint and modulation limits.
    CONF_MINIMUM_SETPOINT: 10,
    CONF_MAXIMUM_RELATIVE_MODULATION: 100,
    CONF_FORCE_PULSE_WIDTH_MODULATION: False,

    CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS: 18.0,
    CONF_MODULATION_SUPPRESSION_DELAY_SECONDS: 20,
    CONF_MODULATION_SUPPRESSION_OFFSET_CELSIUS: 1.0,

    # Consumption bounds.
    CONF_MINIMUM_CONSUMPTION: 0,
    CONF_MAXIMUM_CONSUMPTION: 0,

    # Timing and step configuration.
    CONF_DUTY_CYCLE: "00:13:00",
    CONF_CLIMATE_VALVE_OFFSET: 0,
    CONF_TARGET_TEMPERATURE_STEP: 0.5,
    CONF_SENSOR_MAX_VALUE_AGE: "06:00:00",
    CONF_SIMULATED_WARMING_UP: "00:00:15",
    CONF_WINDOW_MINIMUM_OPEN_TIME: "00:00:15",

    # Preset temperatures.
    CONF_ACTIVITY_TEMPERATURE: 10,
    CONF_AWAY_TEMPERATURE: 10,
    CONF_HOME_TEMPERATURE: 18,
    CONF_SLEEP_TEMPERATURE: 15,
    CONF_COMFORT_TEMPERATURE: 20,

    # Heating system defaults.
    CONF_HEATING_CURVE_COEFFICIENT: 2.0,
    CONF_HEATING_MODE: HeatingMode.COMFORT,
    CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
}

# Constants and defaults for overshoot protection logic.
OVERSHOOT_PROTECTION_REQUIRED_DATASET = 40
OVERSHOOT_PROTECTION_SETPOINT = {
    HeatingSystem.HEAT_PUMP: 40,
    HeatingSystem.RADIATORS: 62,
    HeatingSystem.UNDERFLOOR: 45,
}

# Storage keys for persistent values.
STORAGE_OVERSHOOT_PROTECTION_VALUE = "overshoot_protection_value"

# Service names exposed by the integration.
SERVICE_RESET_INTEGRAL = "reset_integral"
SERVICE_PULSE_WIDTH_MODULATION = "pulse_width_modulation"
SERVICE_SET_OVERSHOOT_PROTECTION_VALUE = "set_overshoot_protection_value"
SERVICE_START_OVERSHOOT_PROTECTION_CALCULATION = "start_overshoot_protection_calculation"

# Config flow step identifiers.
STEP_SETUP_GATEWAY = "gateway"
STEP_SETUP_SENSORS = "sensors"

# Event names emitted on cycle lifecycle changes.
EVENT_SAT_CYCLE_STARTED = "sat_cycle_started"
EVENT_SAT_CYCLE_ENDED = "sat_cycle_ended"

# Dispatcher signals for internal updates.
SIGNAL_PID_UPDATED = "sat_pid_updated"

# Classification thresholds/sets for cycle health.
UNHEALTHY_CYCLES = (
    CycleClassification.LONG_OVERSHOOT,
    CycleClassification.LONG_UNDERHEAT,
    CycleClassification.FAST_OVERSHOOT,
    CycleClassification.FAST_UNDERHEAT,
    CycleClassification.TOO_SHORT_UNDERHEAT,
    CycleClassification.TOO_SHORT_OVERSHOOT,
)
