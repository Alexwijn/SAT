from __future__ import annotations

from types import MappingProxyType
from typing import Any, Optional, TYPE_CHECKING

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


def create_pid_controller(config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any], entity_id: Optional[str] = None) -> PID:
    """Create and return a PID controller instance with the given configuration options."""
    # Extract the configuration options
    kp = float(config_options.get(CONF_PROPORTIONAL))
    ki = float(config_options.get(CONF_INTEGRAL))
    kd = float(config_options.get(CONF_DERIVATIVE))

    heating_system = config_data.get(CONF_HEATING_SYSTEM)
    automatic_gains = bool(config_options.get(CONF_AUTOMATIC_GAINS))
    automatic_gains_value = float(config_options.get(CONF_AUTOMATIC_GAINS_VALUE))
    heating_curve_coefficient = float(config_options.get(CONF_HEATING_CURVE_COEFFICIENT))

    # Return a new PID controller instance with the given configuration options
    return PID(
        heating_system=heating_system,
        automatic_gain_value=automatic_gains_value,
        heating_curve_coefficient=heating_curve_coefficient,

        kp=kp, ki=ki, kd=kd,
        entity_id=entity_id,
        automatic_gains=automatic_gains,
    )


def create_dynamic_minimum_setpoint_controller(_config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any]) -> DynamicMinimumSetpoint:
    """Create and return a Dynamic Minimum Setpoint controller instance with the given configuration options."""
    # Return a new Minimum Setpoint controller instance with the given configuration options
    return DynamicMinimumSetpoint(
        config=MinimumSetpointConfig(
            minimum_setpoint=config_options.get(CONF_MINIMUM_SETPOINT),
            maximum_setpoint=config_options.get(CONF_MAXIMUM_SETPOINT)
        )
    )


def create_heating_curve_controller(config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any]) -> HeatingCurve:
    """Create and return a Heating Curve controller instance with the given configuration options."""
    # Extract the configuration options
    heating_system = config_data.get(CONF_HEATING_SYSTEM)
    coefficient = float(config_options.get(CONF_HEATING_CURVE_COEFFICIENT))

    # Return a new Heating Curve controller instance with the given configuration options
    return HeatingCurve(heating_system=heating_system, coefficient=coefficient)


def create_pwm_controller(heating_curve: HeatingCurve, _config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any]) -> Optional[PWM]:
    """Create and return a PWM controller instance with the given configuration options."""
    # Extract the configuration options
    max_duty_cycles = int(config_options.get(CONF_CYCLES_PER_HOUR))
    max_cycle_time = int(convert_time_str_to_seconds(config_options.get(CONF_DUTY_CYCLE)))

    # Extra settings
    cycles = PWMConfig(maximum_cycles=max_duty_cycles, maximum_cycle_time=max_cycle_time)

    # Return a new PWM controller instance with the given configuration options
    return PWM(heating_curve=heating_curve, config=cycles)


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
