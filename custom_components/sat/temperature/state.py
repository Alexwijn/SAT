from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TemperatureState:
    """Represents a temperature state and error value for an entity or area."""
    entity_id: str
    current: float
    setpoint: float
    last_updated: datetime
    last_changed: datetime
    last_reported: datetime

    @property
    def error(self) -> float:
        """Return the temperature error."""
        return round(self.setpoint - self.current, 3)
