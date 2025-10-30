from __future__ import annotations

from types import MappingProxyType
from typing import Any
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry

from .const import *
from .heating_curve import HeatingCurve
from .helpers import convert_time_str_to_seconds
from .minimum_setpoint import MinimumSetpoint
from .pid import PID
from .pwm import PWM, Cycles

if TYPE_CHECKING:
    from .climate import SatClimate


def create_pid_controller(config_options) -> PID:
    """Create and return a PID controller instance with the given configuration options."""
    # Extract the configuration options
    kp = float(config_options.get(CONF_PROPORTIONAL))
    ki = float(config_options.get(CONF_INTEGRAL))
    kd = float(config_options.get(CONF_DERIVATIVE))

    heating_system = config_options.get(CONF_HEATING_SYSTEM)
    version = int(config_options.get(CONF_PID_CONTROLLER_VERSION))
    automatic_gains = bool(config_options.get(CONF_AUTOMATIC_GAINS))
    automatic_gains_value = float(config_options.get(CONF_AUTOMATIC_GAINS_VALUE))
    derivative_time_weight = float(config_options.get(CONF_DERIVATIVE_TIME_WEIGHT))
    heating_curve_coefficient = float(config_options.get(CONF_HEATING_CURVE_COEFFICIENT))
    sample_time_limit = convert_time_str_to_seconds(config_options.get(CONF_SAMPLE_TIME))

    # Return a new PID controller instance with the given configuration options
    return PID(
        version=version,
        heating_system=heating_system,
        automatic_gain_value=automatic_gains_value,
        derivative_time_weight=derivative_time_weight,
        heating_curve_coefficient=heating_curve_coefficient,

        kp=kp, ki=ki, kd=kd,
        automatic_gains=automatic_gains,
        sample_time_limit=sample_time_limit
    )


def create_minimum_setpoint_controller(config_data, config_options) -> MinimumSetpoint:
    """Create and return a Minimum Setpoint controller instance with the given configuration options."""
    # Extract the configuration options
    minimum_setpoint = config_data.get(CONF_MINIMUM_SETPOINT)
    adjustment_factor = config_options.get(CONF_MINIMUM_SETPOINT_ADJUSTMENT_FACTOR)

    # Return a new Minimum Setpoint controller instance with the given configuration options
    return MinimumSetpoint(configured_minimum_setpoint=minimum_setpoint, adjustment_factor=adjustment_factor)


def create_heating_curve_controller(config_data, config_options) -> HeatingCurve:
    """Create and return a Heating Curve controller instance with the given configuration options."""
    # Extract the configuration options
    heating_system = config_data.get(CONF_HEATING_SYSTEM)
    coefficient = float(config_options.get(CONF_HEATING_CURVE_COEFFICIENT))

    # Return a new Heating Curve controller instance with the given configuration options
    return HeatingCurve(heating_system=heating_system, coefficient=coefficient)


def create_pwm_controller(heating_curve: HeatingCurve, supports_relative_modulation_management: bool, config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any]) -> PWM | None:
    """Create and return a PWM controller instance with the given configuration options."""
    # Extract the configuration options
    max_duty_cycles = int(config_options.get(CONF_CYCLES_PER_HOUR))
    automatic_duty_cycle = bool(config_options.get(CONF_AUTOMATIC_DUTY_CYCLE))
    max_cycle_time = int(convert_time_str_to_seconds(config_options.get(CONF_DUTY_CYCLE)))
    force = bool(config_data.get(CONF_MODE) == MODE_SWITCH) or bool(config_options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))

    # Extra settings
    cycles = Cycles(maximum=max_duty_cycles, maximum_time=max_cycle_time)

    # Return a new PWM controller instance with the given configuration options
    return PWM(heating_curve=heating_curve, cycles=cycles, automatic_duty_cycle=automatic_duty_cycle, supports_relative_modulation_management=supports_relative_modulation_management, force=force)


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
