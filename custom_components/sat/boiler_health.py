"""Long-term boiler health tracking built on daily rollups."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from statistics import fmean, median
from typing import Deque, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .cycles import Cycle
from .device import DeviceState
from .entry_data import SatConfig
from .types import CycleKind

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_SAVE_INTERVAL_SECONDS = 300.0

HISTORY_RETENTION_DAYS = 400
PRESSURE_SAMPLE_INTERVAL_SECONDS = 600.0
COLD_BOILER_MAXIMUM_CELSIUS = 35.0

PRESSURE_TREND_WINDOW_DAYS = 90
PRESSURE_TREND_MINIMUM_DAYS = 14
PRESSURE_TREND_MINIMUM_LOSS_BAR = 0.3
PRESSURE_TREND_MINIMUM_SLOPE_BAR_PER_DAY = 0.01
PRESSURE_TOP_UP_MINIMUM_RISE_BAR = 0.2
PRESSURE_TOP_UP_PROBLEM_COUNT = 2


@dataclass(frozen=True, slots=True)
class BoilerHealthDay:
    """Summary of a single completed day of boiler operation."""

    date: str
    cycle_count: int = 0
    cold_pressure: Optional[float] = None
    median_on_seconds: Optional[float] = None
    flow_return_delta: Optional[float] = None
    outside_temperature: Optional[float] = None

    @property
    def ordinal(self) -> int:
        return date.fromisoformat(self.date).toordinal()


@dataclass(slots=True)
class BoilerHealthAccumulator:
    """Raw values collected during the current day."""

    date: str
    pressures: list[float] = field(default_factory=list)
    on_seconds: list[float] = field(default_factory=list)
    flow_return_deltas: list[float] = field(default_factory=list)
    outside_temperatures: list[float] = field(default_factory=list)

    def to_day(self) -> BoilerHealthDay:
        return BoilerHealthDay(
            date=self.date,
            cycle_count=len(self.on_seconds),
            cold_pressure=median(self.pressures) if self.pressures else None,
            median_on_seconds=median(self.on_seconds) if self.on_seconds else None,
            flow_return_delta=median(self.flow_return_deltas) if self.flow_return_deltas else None,
            outside_temperature=fmean(self.outside_temperatures) if self.outside_temperatures else None,
        )

    def clear_cycles(self) -> None:
        self.on_seconds.clear()
        self.flow_return_deltas.clear()
        self.outside_temperatures.clear()


@dataclass(frozen=True, slots=True)
class PressureTrend:
    """Cold pressure development since the last top-up."""

    days: int = 0
    top_ups: int = 0
    loss_bar: Optional[float] = None
    slope_bar_per_day: Optional[float] = None

    @property
    def is_slow_loss(self) -> bool:
        if self.loss_bar is None or self.slope_bar_per_day is None:
            return False

        return self.loss_bar >= PRESSURE_TREND_MINIMUM_LOSS_BAR and self.slope_bar_per_day >= PRESSURE_TREND_MINIMUM_SLOPE_BAR_PER_DAY

    @property
    def is_problem(self) -> bool:
        return self.is_slow_loss or self.top_ups >= PRESSURE_TOP_UP_PROBLEM_COUNT


@dataclass(frozen=True, slots=True)
class BoilerTrends:
    """Evaluated long-term trends of the boiler."""

    pressure: PressureTrend = PressureTrend()

    @property
    def is_problem(self) -> bool:
        return self.pressure.is_problem


class BoilerTrendEvaluator:
    """Derive long-term trends from completed days."""

    @staticmethod
    def evaluate(days: list[BoilerHealthDay], today: int) -> BoilerTrends:
        return BoilerTrends(pressure=BoilerTrendEvaluator.pressure(days, today))

    @staticmethod
    def pressure(days: list[BoilerHealthDay], today: int) -> PressureTrend:
        measured = [day for day in days if day.cold_pressure is not None and day.ordinal >= today - PRESSURE_TREND_WINDOW_DAYS]

        top_up_indexes = [
            index for index in range(1, len(measured))
            if measured[index].cold_pressure - measured[index - 1].cold_pressure >= PRESSURE_TOP_UP_MINIMUM_RISE_BAR
        ]

        segment = measured[top_up_indexes[-1]:] if top_up_indexes else measured

        if len(segment) < PRESSURE_TREND_MINIMUM_DAYS:
            return PressureTrend(days=len(segment), top_ups=len(top_up_indexes))

        slope = BoilerTrendEvaluator._slope(segment)
        span = segment[-1].ordinal - segment[0].ordinal

        return PressureTrend(days=len(segment), top_ups=len(top_up_indexes), loss_bar=round(-slope * span, 3), slope_bar_per_day=round(-slope, 4))

    @staticmethod
    def _slope(days: list[BoilerHealthDay]) -> float:
        mean_ordinal = fmean(day.ordinal for day in days)
        mean_pressure = fmean(day.cold_pressure for day in days)

        numerator = sum((day.ordinal - mean_ordinal) * (day.cold_pressure - mean_pressure) for day in days)
        denominator = sum((day.ordinal - mean_ordinal) ** 2 for day in days)

        return numerator / denominator if denominator > 0 else 0.0


class BoilerHealthMonitor:
    """Keep a daily history of boiler behaviour that survives restarts."""

    def __init__(self, config: SatConfig) -> None:
        self._fingerprint: str = f"{config.pid}|{config.pwm}"

        self._store: Optional[Store] = None
        self._hass: Optional[HomeAssistant] = None
        self._saved_at: Optional[float] = None
        self._trends: Optional[BoilerTrends] = None
        self._pressure_sampled_at: Optional[float] = None
        self._current: Optional[BoilerHealthAccumulator] = None
        self._days: Deque[BoilerHealthDay] = deque()

    async def async_added_to_hass(self, hass: HomeAssistant, entry_id: str) -> None:
        """Restore the daily history from storage when the integration loads."""
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, f"sat.boiler_health.{entry_id}")

        if not (data := await self._store.async_load()):
            return

        self._days = deque(BoilerHealthDay(**day) for day in data.get("days", []))

        if (current := data.get("current")) is not None:
            self._current = BoilerHealthAccumulator(**current)

        if data.get("fingerprint") != self._fingerprint:
            self._clear_cycle_history()

        _LOGGER.debug("Restored %d days of boiler health history.", len(self._days))

    async def async_save_data(self) -> None:
        """Persist the history immediately."""
        if self._store is not None:
            await self._store.async_save(self._data_to_save())

    @property
    def days(self) -> list[BoilerHealthDay]:
        """Completed days, oldest first."""
        return list(self._days)

    @property
    def trends(self) -> BoilerTrends:
        """Trends over the completed days, refreshed when a day completes."""
        if self._current is None:
            return BoilerTrends()

        if self._trends is None:
            self._trends = BoilerTrendEvaluator.evaluate(self.days, date.fromisoformat(self._current.date).toordinal())

        return self._trends

    def update(self, state: DeviceState, pressure: Optional[float], now: float) -> None:
        """Track the day rollover and sample the pressure while the boiler is cold."""
        accumulator = self._accumulator(now)

        if pressure is None or state.flame_active or state.flow_temperature is None:
            return

        if state.flow_temperature > COLD_BOILER_MAXIMUM_CELSIUS:
            return

        if self._pressure_sampled_at is not None and (now - self._pressure_sampled_at) < PRESSURE_SAMPLE_INTERVAL_SECONDS:
            return

        self._pressure_sampled_at = now
        accumulator.pressures.append(pressure)

        self._schedule_save(now)

    def record_cycle(self, cycle: Cycle, outside_temperature: Optional[float]) -> None:
        """Add a completed central heating cycle to the current day."""
        if cycle.kind != CycleKind.CENTRAL_HEATING:
            return

        accumulator = self._accumulator(cycle.end)
        accumulator.on_seconds.append(cycle.duration)

        if (delta := cycle.metrics.flow_return_delta.p50) is not None:
            accumulator.flow_return_deltas.append(delta)

        if outside_temperature is not None:
            accumulator.outside_temperatures.append(outside_temperature)

        self._schedule_save(cycle.end)

    def _accumulator(self, now: float) -> BoilerHealthAccumulator:
        today = dt_util.as_local(dt_util.utc_from_timestamp(now)).date()

        if self._current is None:
            self._current = BoilerHealthAccumulator(date=today.isoformat())

        elif self._current.date != today.isoformat():
            self._days.append(self._current.to_day())
            self._current = BoilerHealthAccumulator(date=today.isoformat())
            self._trends = None

            self._prune(today)
            self._schedule_save(now)

        return self._current

    def _prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()

        while self._days and self._days[0].date < cutoff:
            self._days.popleft()

    def _clear_cycle_history(self) -> None:
        self._trends = None
        self._days = deque(
            replace(day, cycle_count=0, median_on_seconds=None, flow_return_delta=None, outside_temperature=None)
            for day in self._days
        )

        if self._current is not None:
            self._current.clear_cycles()

    def _schedule_save(self, now: float) -> None:
        if self._hass is None or self._store is None:
            return

        if self._saved_at is not None and (now - self._saved_at) < STORAGE_SAVE_INTERVAL_SECONDS:
            return

        self._saved_at = now
        data = self._data_to_save()
        self._hass.loop.call_soon_threadsafe(self._store.async_delay_save, lambda: data)

    def _data_to_save(self) -> dict:
        current = self._current

        return {
            "fingerprint": self._fingerprint,
            "days": [
                {
                    "date": day.date,
                    "cycle_count": day.cycle_count,
                    "cold_pressure": day.cold_pressure,
                    "median_on_seconds": day.median_on_seconds,
                    "flow_return_delta": day.flow_return_delta,
                    "outside_temperature": day.outside_temperature,
                }
                for day in list(self._days)
            ],
            "current": None if current is None else {
                "date": current.date,
                "pressures": list(current.pressures),
                "on_seconds": list(current.on_seconds),
                "flow_return_deltas": list(current.flow_return_deltas),
                "outside_temperatures": list(current.outside_temperatures),
            },
        }
