"""Measure the weather-corrected heat demand of the house from long-term statistics."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, timedelta
from statistics import median
from typing import Callable, Optional

from homeassistant.const import UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .helpers import float_value

_LOGGER = logging.getLogger(__name__)

ENERGY_DOMAIN = "energy"
RECORDER_DOMAIN = "recorder"

SOURCE_METER = "meter"
SOURCE_ESTIMATE = "estimate"

HISTORY_DAYS = 800
REFRESH_INTERVAL = timedelta(hours=24)
STARTUP_DELAY_SECONDS = 60

DEGREE_DAY_BASE_CELSIUS = 18.0

WINDOW_DAYS = 365
MINIMUM_DAYS = 30
MINIMUM_DEGREE_DAYS = 300.0
COMPARISON_MINIMUM_DAYS = 329

BASELOAD_MINIMUM_DAYS = 14
BASELOAD_MAXIMUM_DEGREE_DAYS = 1.0

ESTIMATE_RATIO_MINIMUM_DAYS = 14


@dataclass(frozen=True, slots=True)
class EfficiencyDay:
    """Gas use and heating need of a single day."""

    date: date
    gas: float
    degree_days: float


@dataclass(frozen=True, slots=True)
class EfficiencyPeriod:
    """Gas needed for heating over a period, corrected for the weather."""

    days: int
    baseload: float
    degree_days: float
    gas_per_degree_day: float


@dataclass(frozen=True, slots=True)
class GasHistory:
    """Daily gas statistics and where they came from."""

    source: str
    unit: Optional[str]
    rows: list[dict]


@dataclass(frozen=True, slots=True)
class EfficiencyReport:
    """Current heat demand compared with the same period a year earlier."""

    source: Optional[str] = None
    unit: Optional[str] = None
    current: Optional[EfficiencyPeriod] = None
    previous: Optional[EfficiencyPeriod] = None
    gas_estimate_ratio: Optional[float] = None

    @property
    def change_percentage(self) -> Optional[float]:
        if self.current is None or self.previous is None or self.previous.gas_per_degree_day <= 0:
            return None

        if self.current.days < COMPARISON_MINIMUM_DAYS or self.previous.days < COMPARISON_MINIMUM_DAYS:
            return None

        return round((self.current.gas_per_degree_day - self.previous.gas_per_degree_day) / self.previous.gas_per_degree_day * 100, 1)


class EfficiencyEvaluator:
    """Derive the efficiency report from daily statistics."""

    @staticmethod
    def report(gas: GasHistory, temperatures: dict[date, float], today: date) -> EfficiencyReport:
        days = EfficiencyEvaluator.days(gas.rows, temperatures)

        return EfficiencyReport(
            unit=gas.unit,
            source=gas.source,
            current=EfficiencyEvaluator.period(EfficiencyEvaluator._window(days, today)),
            previous=EfficiencyEvaluator.period(EfficiencyEvaluator._window(days, today - timedelta(days=WINDOW_DAYS))),
        )

    @staticmethod
    def days(gas_rows: list[dict], temperatures: dict[date, float]) -> list[EfficiencyDay]:
        """Pair the gas use of each day with its heating need, dropping days that cannot be trusted."""
        days = []

        for day, gas in EfficiencyEvaluator.daily_gas(gas_rows).items():
            if (temperature := temperatures.get(day)) is not None:
                days.append(EfficiencyDay(date=day, gas=gas, degree_days=max(0.0, DEGREE_DAY_BASE_CELSIUS - temperature)))

        return days

    @staticmethod
    def daily_gas(rows: list[dict]) -> dict[date, float]:
        daily = {}

        for row in rows:
            if (change := float_value(row.get("change"))) is not None and change >= 0:
                daily[EfficiencyEvaluator._date(row)] = change

        return daily

    @staticmethod
    def temperatures(sources: list[list[dict]]) -> dict[date, float]:
        """Combine temperature statistics, where earlier sources take precedence."""
        merged: dict[date, float] = {}

        for rows in sources:
            for row in rows:
                if (mean := float_value(row.get("mean"))) is not None:
                    merged.setdefault(EfficiencyEvaluator._date(row), mean)

        return merged

    @staticmethod
    def period(days: list[EfficiencyDay]) -> Optional[EfficiencyPeriod]:
        degree_days = sum(day.degree_days for day in days)

        if len(days) < MINIMUM_DAYS or degree_days < MINIMUM_DEGREE_DAYS:
            return None

        baseload = EfficiencyEvaluator._baseload(days)
        heating_gas = max(0.0, sum(day.gas - baseload for day in days))

        return EfficiencyPeriod(
            days=len(days),
            baseload=round(baseload, 3),
            degree_days=round(degree_days, 1),
            gas_per_degree_day=round(heating_gas / degree_days, 3),
        )

    @staticmethod
    def _baseload(days: list[EfficiencyDay]) -> float:
        """Return the gas used per day when there is nothing to heat, such as for hot water and cooking."""
        warm_days = [day.gas for day in days if day.degree_days < BASELOAD_MAXIMUM_DEGREE_DAYS]

        return median(warm_days) if len(warm_days) >= BASELOAD_MINIMUM_DAYS else 0.0

    @staticmethod
    def gas_estimate_ratio(meter_rows: list[dict], estimate_rows: list[dict], today: date) -> Optional[float]:
        """Return how much the real meter measured for each unit SAT estimated."""
        meter = EfficiencyEvaluator.daily_gas(meter_rows)
        estimate = EfficiencyEvaluator.daily_gas(estimate_rows)
        shared = [day for day in meter if day in estimate and day < today]

        if len(shared) < ESTIMATE_RATIO_MINIMUM_DAYS:
            return None

        if (estimated := sum(estimate[day] for day in shared)) <= 0:
            return None

        return round(sum(meter[day] for day in shared) / estimated, 3)

    @staticmethod
    def _window(days: list[EfficiencyDay], end: date) -> list[EfficiencyDay]:
        return [day for day in days if end - timedelta(days=WINDOW_DAYS) <= day.date < end]

    @staticmethod
    def _date(row: dict) -> date:
        return dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date()


class EfficiencyMonitor:
    """Refresh the efficiency report from the recorder once a day."""

    def __init__(self, entry_id: str, outside_entity_ids: list[str]) -> None:
        self._entry_id = entry_id
        self._outside_entity_ids = [entity_id for entity_id in outside_entity_ids if entity_id.startswith("sensor.")]

        self._hass: Optional[HomeAssistant] = None
        self._report: EfficiencyReport = EfficiencyReport()
        self._cancel_startup: Optional[Callable[[], None]] = None
        self._cancel_interval: Optional[Callable[[], None]] = None

    @property
    def report(self) -> EfficiencyReport:
        """Return the most recent report."""
        return self._report

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
        """Replace the report with one based on the current statistics."""
        if self._hass is None or RECORDER_DOMAIN not in self._hass.config.components:
            return

        try:
            self._report = await self._async_build_report()
        except Exception as exception:
            _LOGGER.warning("Unable to load statistics for the efficiency report: %s", exception)

    async def _async_scheduled_refresh(self, _now) -> None:
        await self.async_refresh()

    async def _async_build_report(self) -> EfficiencyReport:
        registry = er.async_get(self._hass)
        meter_id = await self._async_energy_gas_statistic()
        estimate_id = registry.async_get_entity_id("sensor", DOMAIN, f"{self._entry_id}-boiler-total-consumption")
        mirror_id = registry.async_get_entity_id("sensor", DOMAIN, f"{self._entry_id}-outside-temperature")

        if (gas_id := meter_id or estimate_id) is None:
            return EfficiencyReport()

        statistic_ids = {gas_id, *self._outside_entity_ids}
        statistic_ids.update(item for item in (estimate_id, mirror_id) if item is not None)

        rows = await self._async_load_statistics(statistic_ids)
        unit = await self._async_unit(gas_id)
        today = dt_util.now().date()

        sensors = sorted((rows[item] for item in self._outside_entity_ids if rows.get(item)), key=lambda items: items[0]["start"])
        temperatures = EfficiencyEvaluator.temperatures([*sensors, rows.get(mirror_id, [])])

        gas = GasHistory(source=SOURCE_METER if meter_id else SOURCE_ESTIMATE, unit=unit, rows=rows.get(gas_id, []))
        report = EfficiencyEvaluator.report(gas, temperatures, today)

        if meter_id is None or estimate_id is None or unit != UnitOfVolume.CUBIC_METERS:
            return report

        return replace(report, gas_estimate_ratio=EfficiencyEvaluator.gas_estimate_ratio(rows.get(meter_id, []), rows.get(estimate_id, []), today))

    async def _async_energy_gas_statistic(self) -> Optional[str]:
        if ENERGY_DOMAIN not in self._hass.config.components:
            return None

        # The energy integration may be absent, so it can only be imported once it is known to be loaded.
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(self._hass)
        if manager.data is None:
            return None

        for source in manager.data["energy_sources"]:
            if source["type"] == "gas":
                return source["stat_energy_from"]

        return None

    async def _async_load_statistics(self, statistic_ids: set[str]) -> dict[str, list[dict]]:
        # The recorder and its requirements may be absent, so they can only be imported once it is known to be loaded.
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        return await get_instance(self._hass).async_add_executor_job(
            statistics_during_period,
            self._hass,
            dt_util.utcnow() - timedelta(days=HISTORY_DAYS),
            dt_util.start_of_local_day(),
            statistic_ids,
            "day",
            {"temperature": UnitOfTemperature.CELSIUS},
            {"change", "mean"},
        )

    async def _async_unit(self, statistic_id: str) -> Optional[str]:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import get_metadata

        metadata = await get_instance(self._hass).async_add_executor_job(lambda: get_metadata(self._hass, statistic_ids={statistic_id}))

        return metadata[statistic_id][1]["unit_of_measurement"] if statistic_id in metadata else None
