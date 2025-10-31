from dataclasses import dataclass
from typing import Optional, Iterable, Iterator


class Errors:
    """A collection of ErrorInfo objects with utility methods."""

    def __init__(self, errors: Optional[Iterable["Error"]] = None) -> None:
        self._errors: list["Error"] = list(errors) if errors else []

    def __iter__(self) -> Iterator["Error"]:
        return iter(self._errors)

    def __len__(self) -> int:
        return len(self._errors)

    def __getitem__(self, index: int) -> "Error":
        return self._errors[index]

    def __add__(self, other: "Errors") -> "Errors":
        return self.merge(other)

    def add(self, error: "Error") -> None:
        self._errors.append(error)

    def merge(self, other: "Errors") -> "Errors":
        return Errors(self._errors + other._errors)

    def max(self) -> Optional["Error"]:
        return max(self._errors, key=lambda e: e.value)


@dataclass(frozen=True, slots=True)
class Error:
    """Represents an error value associated with an entity or area."""
    entity_id: str
    value: float
