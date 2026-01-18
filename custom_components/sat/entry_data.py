from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Optional, TYPE_CHECKING

from sentry_sdk import Client

from .const import *
from .helpers import calculate_default_maximum_setpoint, convert_time_str_to_seconds, float_value
from .types import HeatingSystem, HeatingMode

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .climate import SatClimate
    from .heating_control import SatHeatingControl
    from .coordinator import SatDataUpdateCoordinator


class SatMode(StrEnum):
    FAKE = "fake"
    MQTT_EMS = "mqtt_ems"
    MQTT_OPENTHERM = "mqtt_opentherm"
    MQTT_OTTHING = "mqtt_otthing"
    SERIAL = "serial"
    ESPHOME = "esphome"
    SIMULATOR = "simulator"
    SWITCH = "switch"


@dataclass(frozen=True)
class SensorsConfig:
    inside_sensor_entity_id: str
    humidity_sensor_entity_id: Optional[str]
    outside_sensor_entity_id: list[str] | str

    sensor_max_value_age_seconds: float
    window_minimum_open_time_seconds: float


@dataclass(frozen=True)
class PidConfig:
    integral: float
    derivative: float
    proportional: float
    automatic_gains: bool
    automatic_gains_value: float
    heating_curve_coefficient: float


@dataclass(frozen=True)
class PwmConfig:
    cycles_per_hour: int
    duty_cycle_seconds: int
    force_pulse_width_modulation: bool
    maximum_relative_modulation: int


@dataclass(frozen=True)
class LimitsConfig:
    minimum_setpoint: float
    maximum_setpoint: float
    maximum_consumption: float
    minimum_consumption: float
    climate_valve_offset: float
    target_temperature_step: float


@dataclass(frozen=True)
class PresetConfig:
    heating_mode: HeatingMode
    thermal_comfort: bool
    sync_climates_with_mode: bool
    sync_climates_with_preset: bool

    presets: Mapping[str, float]
    room_weights: Mapping[str, float]


@dataclass(frozen=True)
class SimulationConfig:
    enabled: bool
    simulated_heating: float
    simulated_cooling: float
    simulated_warming_up_seconds: float


@dataclass(frozen=True)
class SatConfig:
    entry_id: str
    data: Mapping[str, Any]
    options: Mapping[str, Any]

    @property
    def device(self) -> Optional[str]:
        return self.data.get(CONF_DEVICE)

    @property
    def error_monitoring_enabled(self) -> bool:
        return bool(self.options.get(CONF_ERROR_MONITORING))

    @property
    def heating_system(self) -> HeatingSystem:
        if not self.data.get(CONF_HEATING_SYSTEM):
            return HeatingSystem.UNKNOWN

        return HeatingSystem(self.data.get(CONF_HEATING_SYSTEM, HeatingSystem.UNKNOWN))

    @property
    def manufacturer(self) -> Optional[str]:
        return self.data.get(CONF_MANUFACTURER)

    @property
    def mqtt_topic(self) -> Optional[str]:
        return self.data.get(CONF_MQTT_TOPIC)

    @property
    def mode(self) -> SatMode:
        return SatMode(self.data.get(CONF_MODE))

    def is_mode(self, mode: SatMode) -> bool:
        return self.mode == mode

    @property
    def name(self) -> str:
        return str(self.data.get(CONF_NAME))

    @property
    def name_lower(self) -> str:
        return self.name.lower()

    @property
    def overshoot_protection(self) -> bool:
        return bool(self.data.get(CONF_OVERSHOOT_PROTECTION))

    @property
    def push_setpoint_to_thermostat(self) -> bool:
        return bool(self.data.get(CONF_PUSH_SETPOINT_TO_THERMOSTAT))

    @property
    def radiators(self) -> list[str]:
        return self.data.get(CONF_RADIATORS) or []

    @property
    def rooms(self) -> list[str]:
        return self.data.get(CONF_ROOMS) or []

    @property
    def thermostat(self) -> Optional[str]:
        return self.data.get(CONF_THERMOSTAT)

    @property
    def window_sensors(self) -> list[str]:
        return self.options.get(CONF_WINDOW_SENSORS) or []

    @property
    def flame_off_setpoint_offset_celsius(self) -> float:
        return float_value(self.options.get(CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS))

    @property
    def modulation_suppression_delay_seconds(self) -> float:
        return float_value(self.options.get(CONF_MODULATION_SUPPRESSION_DELAY_SECONDS))

    @property
    def modulation_suppression_offset_celsius(self) -> float:
        return float_value(self.options.get(CONF_MODULATION_SUPPRESSION_OFFSET_CELSIUS))

    @property
    def flow_setpoint_offset_celsius(self) -> float:
        return float_value(self.options.get(CONF_FLOW_SETPOINT_OFFSET_CELSIUS))

    @property
    def sensors(self) -> SensorsConfig:
        return SensorsConfig(
            inside_sensor_entity_id=self.data[CONF_INSIDE_SENSOR_ENTITY_ID],
            humidity_sensor_entity_id=self.data.get(CONF_HUMIDITY_SENSOR_ENTITY_ID),
            outside_sensor_entity_id=self.data[CONF_OUTSIDE_SENSOR_ENTITY_ID],

            window_minimum_open_time_seconds=convert_time_str_to_seconds(
                self.options.get(CONF_WINDOW_MINIMUM_OPEN_TIME)
            ),
            sensor_max_value_age_seconds=convert_time_str_to_seconds(
                self.options.get(CONF_SENSOR_MAX_VALUE_AGE)
            ),
        )

    @property
    def pid(self) -> PidConfig:
        return PidConfig(
            integral=float(self.options.get(CONF_INTEGRAL)),
            derivative=float(self.options.get(CONF_DERIVATIVE)),
            proportional=float(self.options.get(CONF_PROPORTIONAL)),

            automatic_gains=bool(self.options.get(CONF_AUTOMATIC_GAINS)),
            automatic_gains_value=float(self.options.get(CONF_AUTOMATIC_GAINS_VALUE)),
            heating_curve_coefficient=float(self.options.get(CONF_HEATING_CURVE_COEFFICIENT)),
        )

    @property
    def pwm(self) -> PwmConfig:
        return PwmConfig(
            cycles_per_hour=int(self.options.get(CONF_CYCLES_PER_HOUR)),
            duty_cycle_seconds=int(convert_time_str_to_seconds(self.options.get(CONF_DUTY_CYCLE))),

            force_pulse_width_modulation=bool(self.options.get(CONF_FORCE_PULSE_WIDTH_MODULATION)),
            maximum_relative_modulation=int(self.options.get(CONF_MAXIMUM_RELATIVE_MODULATION)),
        )

    @property
    def limits(self) -> LimitsConfig:
        heating_system = self.data.get(CONF_HEATING_SYSTEM, HeatingSystem.RADIATORS)
        maximum_setpoint_value = self.options.get(CONF_MAXIMUM_SETPOINT)

        if maximum_setpoint_value is None:
            maximum_setpoint_value = self.data.get(
                CONF_MAXIMUM_SETPOINT,
                calculate_default_maximum_setpoint(heating_system),
            )

        return LimitsConfig(
            minimum_setpoint=float(self.data.get(CONF_MINIMUM_SETPOINT, self.options.get(CONF_MINIMUM_SETPOINT))),
            maximum_setpoint=float(maximum_setpoint_value),

            maximum_consumption=float(self.options.get(CONF_MAXIMUM_CONSUMPTION)),
            minimum_consumption=float(self.options.get(CONF_MINIMUM_CONSUMPTION)),

            climate_valve_offset=float(self.options.get(CONF_CLIMATE_VALVE_OFFSET)),
            target_temperature_step=float(self.options.get(CONF_TARGET_TEMPERATURE_STEP)),
        )

    @property
    def presets(self) -> PresetConfig:
        heating_mode = HeatingMode(self.options.get(CONF_HEATING_MODE))

        preset_values = {
            CONF_AWAY_TEMPERATURE: self.options.get(CONF_AWAY_TEMPERATURE),
            CONF_HOME_TEMPERATURE: self.options.get(CONF_HOME_TEMPERATURE),
            CONF_SLEEP_TEMPERATURE: self.options.get(CONF_SLEEP_TEMPERATURE),
            CONF_COMFORT_TEMPERATURE: self.options.get(CONF_COMFORT_TEMPERATURE),
            CONF_ACTIVITY_TEMPERATURE: self.options.get(CONF_ACTIVITY_TEMPERATURE),
        }

        return PresetConfig(
            heating_mode=heating_mode,
            thermal_comfort=bool(self.options.get(CONF_THERMAL_COMFORT)),
            sync_climates_with_mode=bool(self.options.get(CONF_SYNC_CLIMATES_WITH_MODE)),
            sync_climates_with_preset=bool(self.options.get(CONF_SYNC_CLIMATES_WITH_PRESET)),

            presets={key: float(value) for key, value in preset_values.items()},
            room_weights=self.options.get(CONF_ROOM_WEIGHTS) or {},
        )

    @property
    def simulation(self) -> SimulationConfig:
        return SimulationConfig(
            enabled=bool(self.options.get(CONF_SIMULATION)),
            simulated_heating=float(self.data.get(CONF_SIMULATED_HEATING, OPTIONS_DEFAULTS[CONF_SIMULATED_HEATING])),
            simulated_cooling=float(self.data.get(CONF_SIMULATED_COOLING, OPTIONS_DEFAULTS[CONF_SIMULATED_COOLING])),
            simulated_warming_up_seconds=convert_time_str_to_seconds(self.data.get(CONF_SIMULATED_WARMING_UP, OPTIONS_DEFAULTS[CONF_SIMULATED_WARMING_UP])),
        )


@dataclass
class SatEntryData:
    config: SatConfig
    coordinator: SatDataUpdateCoordinator
    heating_control: "SatHeatingControl"

    sentry: Optional[Client] = None
    climate: Optional[SatClimate] = None
    climate_ready: asyncio.Event = field(default_factory=asyncio.Event)


def get_entry_data(hass: HomeAssistant, entry_id: str) -> SatEntryData:
    return hass.data[DOMAIN][entry_id]
