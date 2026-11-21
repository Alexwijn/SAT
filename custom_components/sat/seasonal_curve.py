"""Fit the heating curve coefficient over a whole season from long-term statistics."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from math import floor
from statistics import median
from typing import Callable, Optional

from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .helpers import weighted_median

_LOGGER = logging.getLogger(__name__)

RECORDER_DOMAIN = "recorder"

HISTORY_DAYS = 180
REFRESH_INTERVAL = timedelta(hours=1)
STARTUP_DELAY_SECONDS = 60

MINIMUM_HOURS = 48
MINIMUM_DAYS = 5

BIN_WIDTH_CELSIUS = 3
BIN_MINIMUM_HOURS = 12

COMPARISON_MINIMUM_HOURS = 24
MISMATCH_MINIMUM_DIFFERENCE = 0.5

WEIGHT_REFERENCE_CELSIUS = 20.0


@dataclass(frozen=True, slots=True)
class SeasonalCurveHour:
    """Hourly mean of the implied coefficient and the outside temperature it was seen at."""

    start: float
    coefficient: float
    outside_temperature: float


@dataclass(frozen=True, slots=True)
class SeasonalCurveBin:
    """Implied coefficient within one band of outside temperatures."""

    hours: int
    coefficient: float
    minimum_temperature: int


@dataclass(frozen=True, slots=True)
class SeasonalCurveFit:
    """Coefficient fitted over the season and how it differs with the weather."""

    hours: int = 0
    days: int = 0
    coefficient: Optional[float] = None
    cold_coefficient: Optional[float] = None
    mild_coefficient: Optional[float] = None
    bins: list[SeasonalCurveBin] = field(default_factory=list)

    @property
    def is_mismatch(self) -> bool:
        """Return whether one coefficient cannot suit both cold and mild weather."""
        if self.cold_coefficient is None or self.mild_coefficient is None:
            return False

        return abs(self.cold_coefficient - self.mild_coefficient) >= MISMATCH_MINIMUM_DIFFERENCE


class SeasonalCurveEvaluator:
    """Derive the seasonal fit from hourly statistics."""

    @staticmethod
    def pair(coefficients: list[dict], temperatures: list[dict]) -> list[SeasonalCurveHour]:
        outside = {row["start"]: row["mean"] for row in temperatures if row.get("mean") is not None}

        return [
            SeasonalCurveHour(start=row["start"], coefficient=row["mean"], outside_temperature=outside[row["start"]])
            for row in coefficients
            if row.get("mean") is not None and row["start"] in outside
        ]

    @staticmethod
    def fit(hours: list[SeasonalCurveHour]) -> SeasonalCurveFit:
        days = len({dt_util.as_local(dt_util.utc_from_timestamp(hour.start)).date() for hour in hours})

        if len(hours) < MINIMUM_HOURS or days < MINIMUM_DAYS:
            return SeasonalCurveFit(hours=len(hours), days=days)

        coefficient = weighted_median(
            [hour.coefficient for hour in hours],
            [max(1.0, WEIGHT_REFERENCE_CELSIUS - hour.outside_temperature) for hour in hours],
        )

        split = median(hour.outside_temperature for hour in hours)
        cold = [hour for hour in hours if hour.outside_temperature <= split]
        mild = [hour for hour in hours if hour.outside_temperature > split]

        return SeasonalCurveFit(
            days=days,
            hours=len(hours),
            coefficient=round(coefficient, 2),
            bins=SeasonalCurveEvaluator._bins(hours),
            cold_coefficient=SeasonalCurveEvaluator._side_coefficient(cold),
            mild_coefficient=SeasonalCurveEvaluator._side_coefficient(mild),
        )

    @staticmethod
    def _side_coefficient(hours: list[SeasonalCurveHour]) -> Optional[float]:
        if len(hours) < COMPARISON_MINIMUM_HOURS:
            return None

        return round(median(hour.coefficient for hour in hours), 2)

    @staticmethod
    def _bins(hours: list[SeasonalCurveHour]) -> list[SeasonalCurveBin]:
        grouped: dict[int, list[float]] = {}

        for hour in hours:
            minimum_temperature = floor(hour.outside_temperature / BIN_WIDTH_CELSIUS) * BIN_WIDTH_CELSIUS
            grouped.setdefault(minimum_temperature, []).append(hour.coefficient)

        return [
            SeasonalCurveBin(hours=len(coefficients), coefficient=round(median(coefficients), 2), minimum_temperature=minimum_temperature)
            for minimum_temperature, coefficients in sorted(grouped.items())
            if len(coefficients) >= BIN_MINIMUM_HOURS
        ]


class SeasonalCurveMonitor:
    """Refresh the seasonal fit from the recorder every hour."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id
        self._hass: Optional[HomeAssistant] = None
        self._fit: SeasonalCurveFit = SeasonalCurveFit()
        self._cancel_startup: Optional[Callable[[], None]] = None
        self._cancel_interval: Optional[Callable[[], None]] = None

    @property
    def fit(self) -> SeasonalCurveFit:
        """Return the most recent fit."""
        return self._fit

    async def async_added_to_hass(self, hass: HomeAssistant) -> None:
        """Schedule the refreshes; the first waits until the sensors it reads are registered."""
        self._hass = hass
        self._cancel_startup = async_call_later(hass, STARTUP_DELAY_SECONDS, self._async_scheduled_refresh)
        self._cancel_interval = async_track_time_interval(hass, self._async_scheduled_refresh, REFRESH_INTERVAL, cancel_on_shutdown=True)

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the scheduled refreshes."""
        if self._cancel_startup is not None:
            self._cancel_startup()
            self._cancel_startup = None

        if self._cancel_interval is not None:
            self._cancel_interval()
            self._cancel_interval = None

    async def async_refresh(self) -> None:
        """Replace the fit with one based on the current statistics."""
        if self._hass is None or RECORDER_DOMAIN not in self._hass.config.components:
            return

        registry = er.async_get(self._hass)
        coefficient_id = registry.async_get_entity_id("sensor", DOMAIN, f"{self._entry_id}-implied-curve-coefficient")
        temperature_id = registry.async_get_entity_id("sensor", DOMAIN, f"{self._entry_id}-outside-temperature")

        if coefficient_id is None or temperature_id is None:
            return

        try:
            rows = await self._async_load_statistics({coefficient_id, temperature_id})
        except Exception as exception:
            _LOGGER.warning("Unable to load statistics for the seasonal heating curve fit: %s", exception)
            return

        self._fit = SeasonalCurveEvaluator.fit(SeasonalCurveEvaluator.pair(rows.get(coefficient_id, []), rows.get(temperature_id, [])))

    async def _async_scheduled_refresh(self, _now) -> None:
        await self.async_refresh()

    async def _async_load_statistics(self, statistic_ids: set[str]) -> dict[str, list[dict]]:
        # The recorder and its requirements may be absent, so they can only be imported once it is known to be loaded.
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        return await get_instance(self._hass).async_add_executor_job(
            statistics_during_period,
            self._hass,
            dt_util.utcnow() - timedelta(days=HISTORY_DAYS),
            None,
            statistic_ids,
            "hour",
            {"temperature": UnitOfTemperature.CELSIUS},
            {"mean"},
        )
