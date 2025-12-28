from __future__ import annotations

from types import MappingProxyType
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry

from .const import *
from .heating_curve import HeatingCurve
from .helpers import convert_time_str_to_seconds
from .minimum_setpoint import DynamicMinimumSetpoint, MinimumSetpointConfig
from .pid import PID
from .pwm import PWM, PWMConfig

if TYPE_CHECKING:
    from .climate import SatClimate


def create_pid_controller(config_options) -> PID:
    """Create and return a PID controller instance with the given configuration options."""
    # Extract the configuration options
    kp = float(config_options.get(CONF_PROPORTIONAL))
    ki = float(config_options.get(CONF_INTEGRAL))
    kd = float(config_options.get(CONF_DERIVATIVE))

    heating_system = config_options.get(CONF_HEATING_SYSTEM)
    automatic_gains = bool(config_options.get(CONF_AUTOMATIC_GAINS))
    automatic_gains_value = float(config_options.get(CONF_AUTOMATIC_GAINS_VALUE))
    derivative_time_weight = float(config_options.get(CONF_DERIVATIVE_TIME_WEIGHT))
    heating_curve_coefficient = float(config_options.get(CONF_HEATING_CURVE_COEFFICIENT))

    # Return a new PID controller instance with the given configuration options
    return PID(
        heating_system=heating_system,
        automatic_gain_value=automatic_gains_value,
        derivative_time_weight=derivative_time_weight,
        heating_curve_coefficient=heating_curve_coefficient,

        kp=kp, ki=ki, kd=kd,
        automatic_gains=automatic_gains
    )


def create_dynamic_minimum_setpoint_controller(config_data, _config_options) -> DynamicMinimumSetpoint:
    """Create and return a Dynamic Minimum Setpoint controller instance with the given configuration options."""
    # Return a new Minimum Setpoint controller instance with the given configuration options
    return DynamicMinimumSetpoint(config=MinimumSetpointConfig(
        minimum_setpoint=config_data.get(CONF_MINIMUM_SETPOINT),
        maximum_setpoint=config_data.get(CONF_MAXIMUM_SETPOINT)
    ))


def create_heating_curve_controller(config_data, config_options) -> HeatingCurve:
    """Create and return a Heating Curve controller instance with the given configuration options."""
    # Extract the configuration options
    heating_system = config_data.get(CONF_HEATING_SYSTEM)
    coefficient = float(config_options.get(CONF_HEATING_CURVE_COEFFICIENT))

    # Return a new Heating Curve controller instance with the given configuration options
    return HeatingCurve(heating_system=heating_system, coefficient=coefficient)


def create_pwm_controller(heating_curve: HeatingCurve, _config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any]) -> PWM | None:
    """Create and return a PWM controller instance with the given configuration options."""
    # Extract the configuration options
    max_duty_cycles = int(config_options.get(CONF_CYCLES_PER_HOUR))
    automatic_duty_cycle = bool(config_options.get(CONF_AUTOMATIC_DUTY_CYCLE))
    max_cycle_time = int(convert_time_str_to_seconds(config_options.get(CONF_DUTY_CYCLE)))

    # Extra settings
    cycles = PWMConfig(maximum_cycles=max_duty_cycles, maximum_cycle_time=max_cycle_time)

    # Return a new PWM controller instance with the given configuration options
    return PWM(heating_curve=heating_curve, config=cycles, automatic_duty_cycle=automatic_duty_cycle)


def get_climate_entities(hass: "HomeAssistant", entity_ids: list[str]) -> list["SatClimate"]:
    """Retrieve climate entities for the given entity IDs."""
    entities = []
    for entity_id in entity_ids:
        registry = entity_registry.async_get(hass)

        if not (entry := registry.async_get(entity_id)):
            continue

        if not (config_entry := hass.data[DOMAIN].get(entry.config_entry_id)):
            continue

        if not (climate := config_entry.get(CLIMATE)):
            continue

        entities.append(climate)

    return entities
