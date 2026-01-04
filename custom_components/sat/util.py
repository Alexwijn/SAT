from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry

from .const import *
from .entry_data import SatConfig
from .heating_curve import HeatingCurve
from .minimum_setpoint import DynamicMinimumSetpoint, MinimumSetpointConfig
from .pid import PID
from .pwm import PWM, PWMConfig

if TYPE_CHECKING:
    from .climate import SatClimate


def create_pid_controller(config: SatConfig) -> PID:
    """Create and return a PID controller instance with the given configuration options."""
    kp = config.pid.proportional
    ki = config.pid.integral
    kd = config.pid.derivative

    automatic_gains = config.pid.automatic_gains
    automatic_gains_value = config.pid.automatic_gains_value
    heating_curve_coefficient = config.pid.heating_curve_coefficient

    return PID(
        heating_system=config.heating_system,
        automatic_gain_value=automatic_gains_value,
        heating_curve_coefficient=heating_curve_coefficient,
        automatic_gains=automatic_gains,
        kp=kp,
        ki=ki,
        kd=kd,
    )


def create_dynamic_minimum_setpoint_controller(config: SatConfig) -> DynamicMinimumSetpoint:
    """Create and return a Dynamic Minimum Setpoint controller instance with the given configuration options."""
    return DynamicMinimumSetpoint(config=MinimumSetpointConfig(minimum_setpoint=config.limits.minimum_setpoint, maximum_setpoint=config.limits.maximum_setpoint))


def create_heating_curve_controller(config: SatConfig) -> HeatingCurve:
    """Create and return a Heating Curve controller instance with the given configuration options."""
    return HeatingCurve(heating_system=config.heating_system, coefficient=config.pid.heating_curve_coefficient)


def create_pwm_controller(heating_curve: HeatingCurve, config: SatConfig) -> Optional[PWM]:
    """Create and return a PWM controller instance with the given configuration options."""
    max_duty_cycles = config.pwm.cycles_per_hour
    max_cycle_time = config.pwm.duty_cycle_seconds

    cycles = PWMConfig(maximum_cycle_time=max_cycle_time, maximum_cycles=max_duty_cycles)

    return PWM(heating_curve=heating_curve, config=cycles)


def get_climate_entities(hass: "HomeAssistant", entity_ids: list[str]) -> list["SatClimate"]:
    """Retrieve climate entities for the given entity IDs."""
    entities = []
    for entity_id in entity_ids:
        registry = entity_registry.async_get(hass)

        if not (entry := registry.async_get(entity_id)):
            continue

        if not (entry_data := hass.data[DOMAIN].get(entry.config_entry_id)):
            continue

        if entry_data.climate is not None:
            entities.append(entry_data.climate)

    return entities
