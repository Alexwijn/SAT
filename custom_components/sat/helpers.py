import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from re import sub
from time import monotonic
from typing import Optional

from homeassistant.util import dt

from .const import HEATING_SYSTEM_UNDERFLOOR

EPSILON = 1e-3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class State:
    value: Optional[float] = None
    last_changed: datetime = field(default_factory=utcnow)


def update_state(previous: State, new_value: float) -> State:
    """
    Return a new State if the value changed beyond tolerance; otherwise return the existing one.
    Always timezone-aware and safe for float comparisons.
    """
    if previous.value is not None and math.isclose(previous.value, new_value, abs_tol=EPSILON):
        # No significant change → preserve timestamp
        return previous

    # Changed or first assignment → create new State
    return State(value=new_value, last_changed=utcnow())


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


def float_value(value) -> float | None:
    """Safely convert a value to a float, returning None if conversion fails."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp(value: float, low: float, high: Optional[float] = None) -> float:
    """Clamp to [low, high] if high is given, else to [low, +inf).
    :rtype: float
    """
    if high is None:
        return max(low, value)

    return max(low, min(value, high))
