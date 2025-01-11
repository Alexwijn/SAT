from re import sub
from time import monotonic

from homeassistant.util import dt

from .const import HEATING_SYSTEM_UNDERFLOOR


def seconds_since(start_time: float | None) -> float:
    """
    Calculate the elapsed time in seconds since a given start time using monotonic().
    If start_time is None, return 0.

    Args:
        start_time (float or None): The reference start time, typically obtained from time.monotonic().
                                    If None, elapsed time is considered 0.

    Returns:
        float: The elapsed time in seconds as a float. Returns 0 if start_time is None.
    """
    if start_time is None:
        return 0.0

    return monotonic() - start_time


def convert_time_str_to_seconds(time_str: str) -> int:
    """
    Convert a time string in the format 'HH:MM:SS' to seconds.

    Args:
        time_str: A string representing a time in the format 'HH:MM:SS'.

    Returns:
        int: The time in seconds.

    Raises:
        ValueError: If the time string format is invalid.
    """
    try:
        # Parse the input into a valid date time object
        date_time = dt.parse_time(time_str)

        # Calculate the number of seconds
        return round((date_time.hour * 3600) + (date_time.minute * 60) + date_time.second, 0)
    except ValueError as e:
        raise ValueError(f"Invalid time format. Expected 'HH:MM:SS', got '{time_str}'") from e


def calculate_derivative_per_hour(temperature_error: float, time_taken_seconds: float):
    """Calculates the derivative per hour based on the temperature error and time taken."""
    # Convert time taken from seconds to hours
    time_taken_hours = time_taken_seconds / 3600

    # Avoid division-by-zero error
    if time_taken_hours == 0:
        return 0

    # Calculate the derivative per hour by dividing temperature error by time taken
    return round(temperature_error / time_taken_hours, 2)


def calculate_default_maximum_setpoint(heating_system: str) -> int:
    if heating_system == HEATING_SYSTEM_UNDERFLOOR:
        return 50

    return 55


def snake_case(value: str) -> str:
    return '_'.join(
        sub('([A-Z][a-z]+)', r' \1',
            sub('([A-Z]+)', r' \1',
                value.replace('-', ' '))).split()).lower()


def float_value(value) -> float | None:
    """Helper method to convert a value to float, handling possible errors."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
