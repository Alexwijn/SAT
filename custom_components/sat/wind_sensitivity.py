"""Learn how much more heat the house needs in wind from the heating curve advisor days."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from itertools import combinations
from statistics import median
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .helpers import clamp

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

WIND_SENSITIVITY_UPDATE_INTERVAL = timedelta(hours=1)
WIND_SENSITIVITY_RETENTION_DAYS = 120
WIND_SENSITIVITY_MINIMUM_DAYS = 6
WIND_SENSITIVITY_MINIMUM_SPREAD = 15.0
WIND_SENSITIVITY_MINIMUM_PAIR_SPREAD = 5.0
WIND_SENSITIVITY_MAXIMUM = 0.02


@dataclass(frozen=True, slots=True)
class WindDay:
    """Implied heating curve coefficient and median wind speed of one advisor day."""

    date: str
    wind_speed: float
    coefficient: float
    sample_count: int


@dataclass(frozen=True, slots=True)
class WindSensitivity:
    """Heating curve coefficient in calm weather and the extra heat needed per km/h of wind."""

    day_count: int = 0
    wind_spread: Optional[float] = None
    sensitivity: Optional[float] = None
    calm_coefficient: Optional[float] = None

    @property
    def percent_per_10_kmh(self) -> Optional[float]:
        """Return the extra heat needed for every 10 km/h of wind, in percent."""
        return round(self.sensitivity * 1000, 1) if self.sensitivity is not None else None


class WindSensitivityEvaluator:
    """Fit the coefficient of each day as calm coefficient × (1 + sensitivity × wind speed)."""

    @staticmethod
    def spread(days: list[WindDay]) -> Optional[float]:
        """Return how far the median wind of the windiest third of the days lies above the calmest third."""
        if len(days) < 3:
            return None

        speeds = sorted(day.wind_speed for day in days)
        third = len(speeds) // 3

        return round(float(median(speeds[-third:]) - median(speeds[:third])), 1)

    @staticmethod
    def fit(days: list[WindDay]) -> WindSensitivity:
        """Return the robust fit over the days, or only the day count until the wind varied enough."""
        spread = WindSensitivityEvaluator.spread(days)

        if len(days) < WIND_SENSITIVITY_MINIMUM_DAYS or spread is None or spread < WIND_SENSITIVITY_MINIMUM_SPREAD:
            return WindSensitivity(day_count=len(days), wind_spread=spread)

        slopes = [
            (second.coefficient - first.coefficient) / (second.wind_speed - first.wind_speed)
            for first, second in combinations(days, 2)
            if abs(second.wind_speed - first.wind_speed) >= WIND_SENSITIVITY_MINIMUM_PAIR_SPREAD
        ]

        slope = float(median(slopes))
        calm_coefficient = float(median(day.coefficient - slope * day.wind_speed for day in days))

        if calm_coefficient <= 0:
            return WindSensitivity(day_count=len(days), wind_spread=spread)

        return WindSensitivity(
            day_count=len(days),
            wind_spread=spread,
            calm_coefficient=round(calm_coefficient, 2),
            sensitivity=round(clamp(slope / calm_coefficient, 0.0, WIND_SENSITIVITY_MAXIMUM), 4),
        )


class WindSensitivityMonitor:
    """Keep the advisor days with wind for months and fit the wind sensitivity on them."""

    def __init__(self) -> None:
        self._store: Optional[Store] = None
        self._days: dict[str, WindDay] = {}
        self._sensitivity: Optional[WindSensitivity] = None

    async def async_added_to_hass(self, hass: HomeAssistant, entry_id: str) -> None:
        """Restore the kept days from storage when the integration loads."""
        self._store = Store(hass, STORAGE_VERSION, f"sat.wind_sensitivity.{entry_id}")

        if not (data := await self._store.async_load()):
            return

        self._days = {day["date"]: WindDay(**day) for day in data.get("days", [])}
        self._sensitivity = None

        _LOGGER.debug("Restored %d wind sensitivity days.", len(self._days))

    @property
    def days(self) -> list[WindDay]:
        """Kept days, oldest first."""
        return [self._days[key] for key in sorted(self._days)]

    @property
    def sensitivity(self) -> WindSensitivity:
        """Fit over the kept days, refreshed when they change."""
        if self._sensitivity is None:
            self._sensitivity = WindSensitivityEvaluator.fit(self.days)

        return self._sensitivity

    def update(self, days: list[WindDay], today: date) -> None:
        """Keep the most complete estimate of every day and drop the days past retention."""
        cutoff = (today - timedelta(days=WIND_SENSITIVITY_RETENTION_DAYS)).isoformat()
        changed = False

        for day in days:
            if (kept := self._days.get(day.date)) is None or (day.sample_count >= kept.sample_count and day != kept):
                self._days[day.date] = day
                changed = True

        for key in [key for key in self._days if key < cutoff]:
            del self._days[key]
            changed = True

        if not changed:
            return

        self._sensitivity = None

        if self._store is not None:
            self._store.async_delay_save(self._data_to_save)

    def _data_to_save(self) -> dict:
        return {"days": [asdict(day) for day in self.days]}
