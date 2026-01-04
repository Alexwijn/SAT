import math
from datetime import datetime
from re import sub
from typing import Optional, Union, Iterable, Tuple

from homeassistant.core import State
from homeassistant.util import dt

from .const import HeatingSystem


def timestamp() -> float:
    """Return the current wall-clock timestamp in seconds."""
    return dt.utcnow().timestamp()


def event_timestamp(time: Optional[datetime]) -> float:
    """Return a timestamp from an event time, falling back to now."""
    return time.timestamp() if time is not None else timestamp()


def seconds_since(start_time: Optional[float]) -> float:
    """Calculate the elapsed time in seconds since a given start time, returns zero if time is not valid."""
    return timestamp() - start_time if start_time is not None else 0.0


def state_age_seconds(state: State) -> float:
    """Return the age of a HA state in seconds."""
    return (dt.utcnow() - state.last_updated).total_seconds()


def is_state_stale(state: Optional[State], max_age_seconds: float) -> bool:
    """Return True when the state is older than max_age_seconds."""
    if state is None or max_age_seconds <= 0:
        return False

    return state_age_seconds(state) > max_age_seconds


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
    return 50 if heating_system == HeatingSystem.UNDERFLOOR else 55


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


def filter_none(values: Iterable[Optional[float]]) -> list[float]:
    """Return a list with all None values removed."""
    return [value for value in values if value is not None]


def average(values: Iterable[Optional[float]]) -> Optional[float]:
    """Return the arithmetic mean, or None if no values are present."""
    filtered = filter_none(values)
    if not filtered:
        return None

    return sum(filtered) / float(len(filtered))


def min_max(values: Iterable[Optional[float]]) -> Tuple[Optional[float], Optional[float]]:
    """Return (min, max), or (None, None) if no values are present."""
    filtered = filter_none(values)
    if not filtered:
        return None, None

    return min(filtered), max(filtered)


def percentile_interpolated(values: list[float], percentile: float) -> Optional[float]:
    """Return the percentile value of a list of values, or None if the list is empty."""
    if not values:
        return None

    values_sorted = sorted(values)
    if len(values_sorted) == 1:
        return float(values_sorted[0])

    position = (len(values_sorted) - 1) * percentile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(values_sorted) - 1)

    fraction = position - lower_index
    lower_value = values_sorted[lower_index]
    upper_value = values_sorted[upper_index]
    return float(lower_value + (upper_value - lower_value) * fraction)
