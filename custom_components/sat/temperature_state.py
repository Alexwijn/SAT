from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Iterable, Iterator


class TemperatureStates:
    """A collection of TemperatureState objects with utility methods."""

    def __init__(self, errors: Optional[Iterable["TemperatureState"]] = None) -> None:
        self._errors: list["TemperatureState"] = list(errors) if errors else []

    def __iter__(self) -> Iterator["TemperatureState"]:
        return iter(self._errors)

    def __len__(self) -> int:
        return len(self._errors)

    def __getitem__(self, index: int) -> "TemperatureState":
        return self._errors[index]

    def __add__(self, other: "TemperatureStates") -> "TemperatureStates":
        return self.merge(other)

    def add(self, error: "TemperatureState") -> None:
        self._errors.append(error)

    def merge(self, other: "TemperatureStates") -> "TemperatureStates":
        return TemperatureStates(self._errors + other._errors)

    def max(self) -> Optional["TemperatureState"]:
        if not self._errors:
            return None

        return max(self._errors, key=lambda e: e.error)


@dataclass(frozen=True, slots=True)
class TemperatureState:
    """Represents a temperature state and error value for an entity or area."""
    entity_id: str
    current: float
    setpoint: float
    last_updated: datetime
    last_changed: datetime

    @property
    def error(self) -> float:
        """Return the temperature error (setpoint - current)."""
        return round(self.setpoint - self.current, 2)
