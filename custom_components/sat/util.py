from re import sub

from homeassistant.util import dt

from .const import *
from .heating_curve import HeatingCurve
from .pid import PID
from .pwm import PWM


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


def calculate_default_maximum_setpoint(heating_system: str) -> int | None:
    if heating_system == HEATING_SYSTEM_UNDERFLOOR:
        return 50

    if heating_system == HEATING_SYSTEM_RADIATORS:
        return 55

    return None


def create_pid_controller(options) -> PID:
    """Create and return a PID controller instance with the given configuration options."""
    # Extract the configuration options
    kp = float(options.get(CONF_PROPORTIONAL))
    ki = float(options.get(CONF_INTEGRAL))
    kd = float(options.get(CONF_DERIVATIVE))
    automatic_gains = bool(options.get(CONF_AUTOMATIC_GAINS))
    sample_time_limit = convert_time_str_to_seconds(options.get(CONF_SAMPLE_TIME))

    # Return a new PID controller instance with the given configuration options
    return PID(kp=kp, ki=ki, kd=kd, automatic_gains=automatic_gains, sample_time_limit=sample_time_limit)


def create_heating_curve_controller(options) -> HeatingCurve:
    """Create and return a PID controller instance with the given configuration options."""
    # Extract the configuration options
    heating_system = options.get(CONF_HEATING_SYSTEM)
    coefficient = float(options.get(CONF_HEATING_CURVE_COEFFICIENT))

    # Return a new heating Curve controller instance with the given configuration options
    return HeatingCurve(heating_system=heating_system, coefficient=coefficient)


def create_pwm_controller(heating_curve: HeatingCurve, options) -> PWM | None:
    """Create and return a PWM controller instance with the given configuration options."""
    # Extract the configuration options
    automatic_duty_cycle = bool(options.get(CONF_AUTOMATIC_DUTY_CYCLE))
    max_cycle_time = int(convert_time_str_to_seconds(options.get(CONF_DUTY_CYCLE)))
    force = bool(options.get(CONF_MODE) == MODE_SWITCH) or bool(options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))

    # Return a new PWM controller instance with the given configuration options
    return PWM(heating_curve=heating_curve, max_cycle_time=max_cycle_time, automatic_duty_cycle=automatic_duty_cycle, force=force)


def snake_case(s):
    return '_'.join(
        sub('([A-Z][a-z]+)', r' \1',
            sub('([A-Z]+)', r' \1',
                s.replace('-', ' '))).split()).lower()
