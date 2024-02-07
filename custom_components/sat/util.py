from re import sub
from typing import TYPE_CHECKING

from homeassistant.util import dt

from .const import *
from .heating_curve import HeatingCurve
from .minimum_setpoint import MinimumSetpoint
from .pid import PID
from .pwm import PWM

if TYPE_CHECKING:
    pass


def convert_time_str_to_seconds(time_str: str) -> float:
    """Convert a time string in the format 'HH:MM:SS' to seconds.

    Args:
        time_str: A string representing a time in the format 'HH:MM:SS'.

    Returns:
        float: The time in seconds.
    """
    date_time = dt.parse_time(time_str)
    # Calculate the number of seconds by multiplying the hours, minutes and seconds
    return (date_time.hour * 3600) + (date_time.minute * 60) + date_time.second


def calculate_derivative_per_hour(temperature_error: float, time_taken_seconds: float):
    """
    Calculates the derivative per hour based on the temperature error and time taken."""
    # Convert time taken from seconds to hours
    time_taken_hours = time_taken_seconds / 3600
    # Calculate the derivative per hour by dividing temperature error by time taken
    return round(temperature_error / time_taken_hours, 2)


def calculate_default_maximum_setpoint(heating_system: str) -> int:
    if heating_system == HEATING_SYSTEM_UNDERFLOOR:
        return 50

    return 55


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
    sample_time_limit = convert_time_str_to_seconds(config_options.get(CONF_SAMPLE_TIME))

    # Return a new PID controller instance with the given configuration options
    return PID(
        heating_system=heating_system,
        automatic_gain_value=automatic_gains_value,
        derivative_time_weight=derivative_time_weight,

        kp=kp, ki=ki, kd=kd,
        automatic_gains=automatic_gains,
        sample_time_limit=sample_time_limit
    )


def create_heating_curve_controller(config_data, config_options) -> HeatingCurve:
    """Create and return a PID controller instance with the given configuration options."""
    # Extract the configuration options
    heating_system = config_data.get(CONF_HEATING_SYSTEM)
    version = int(config_options.get(CONF_HEATING_CURVE_VERSION))
    coefficient = float(config_options.get(CONF_HEATING_CURVE_COEFFICIENT))

    # Return a new heating Curve controller instance with the given configuration options
    return HeatingCurve(heating_system=heating_system, coefficient=coefficient, version=version)


def create_pwm_controller(heating_curve: HeatingCurve, config_data, config_options) -> PWM | None:
    """Create and return a PWM controller instance with the given configuration options."""
    # Extract the configuration options
    automatic_duty_cycle = bool(config_options.get(CONF_AUTOMATIC_DUTY_CYCLE))
    max_cycle_time = int(convert_time_str_to_seconds(config_options.get(CONF_DUTY_CYCLE)))
    force = bool(config_data.get(CONF_MODE) == MODE_SWITCH) or bool(config_options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))

    # Return a new PWM controller instance with the given configuration options
    return PWM(heating_curve=heating_curve, max_cycle_time=max_cycle_time, automatic_duty_cycle=automatic_duty_cycle, force=force)


def create_minimum_setpoint_controller(config_data, config_options) -> MinimumSetpoint:
    minimum_setpoint = config_data.get(CONF_MINIMUM_SETPOINT)
    adjustment_factor = config_options.get(CONF_MINIMUM_SETPOINT_ADJUSTMENT_FACTOR)

    return MinimumSetpoint(configured_minimum_setpoint=minimum_setpoint, adjustment_factor=adjustment_factor)


def snake_case(s):
    return '_'.join(
        sub('([A-Z][a-z]+)', r' \1',
            sub('([A-Z]+)', r' \1',
                s.replace('-', ' '))).split()).lower()
