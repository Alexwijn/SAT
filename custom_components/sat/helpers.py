import math
from re import sub
from time import monotonic
from typing import Optional, Union

from homeassistant.util import dt

from .const import HEATING_SYSTEM_UNDERFLOOR


def seconds_since(start_time: float | None) -> float:
    """Calculate the elapsed time in seconds since a given start time, returns zero if time is not valid."""
    if start_time is None:
        return 0.0

    return monotonic() - start_time


def convert_time_str_to_seconds(time_str: str) -> int:
    """Convert a time string in the format 'HH:MM:SS' to seconds."""
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
    """Determine the default maximum temperature for a given heating system."""
    if heating_system == HEATING_SYSTEM_UNDERFLOOR:
        return 50

    return 55


def snake_case(value: str) -> str:
    """Transform a string from CamelCase or kebab-case to snake_case."""
    return '_'.join(
        sub('([A-Z][a-z]+)', r' \1',
            sub('([A-Z]+)', r' \1',
                value.replace('-', ' '))).split()).lower()


def float_value(value: Union[int, float, str, None]) -> Optional[float]:
    """Safely convert a value to a finite float, returning None if the conversion fails."""
    if value is None:
        return None

    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def to_float(value: Union[int, float, str, None], default: float = 0.0) -> float:
    """Convert to float, returning default if the conversion fails or the result is None."""
    result = float_value(value)
    return float(result) if result is not None else float(default)


def int_value(value: Union[int, float, str, None]) -> Optional[int]:
    """Safely convert a value to an int, returning None if the conversion fails."""
    result = float_value(value)
    return int(result) if result is not None else None


def to_int(value: Union[int, float, str, None], default: int = 0) -> int:
    """Convert to int, returning default if the conversion fails or the result is None."""
    result = int_value(value)
    return int(result) if result is not None else int(default)


def clamp(value: float, low: float, high: Optional[float] = None) -> float:
    """Clamp to [low, high] if high is given, else to [low, +inf]."""
    if high is None:
        return max(low, value)

    return max(low, min(value, high))
