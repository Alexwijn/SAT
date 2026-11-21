"""Learn how long each room takes to recover, to know when heating has to start."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from math import ceil, floor
from statistics import fmean, median
from typing import Deque, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .helpers import float_value
from .room_balance import BoilerObservation, RoomObservation

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
HISTORY_RETENTION_DAYS = 180

RECOVERY_MINIMUM_SHORTFALL_CELSIUS = 1.0
RECOVERY_FINISHED_SHORTFALL_CELSIUS = 0.1
RECOVERY_MAXIMUM_RISE_CELSIUS = 0.5
RECOVERY_MAXIMUM_SECONDS = 12 * 60 * 60

BAND_WIDTH_CELSIUS = 5
MINIMUM_EPISODES = 3
LEAD_TIME_MARGIN = 1.1
FORECAST_MAXIMUM_GAP_SECONDS = 3 * 60 * 60


@dataclass(frozen=True, slots=True)
class RecoveryEpisode:
    """A completed recovery of a room back to its target."""

    date: str
    room_id: str
    degrees: float
    rate_per_hour: float
    outside_temperature: Optional[float] = None

    @property
    def band(self) -> Optional[int]:
        if self.outside_temperature is None:
            return None

        return floor(self.outside_temperature / BAND_WIDTH_CELSIUS) * BAND_WIDTH_CELSIUS


@dataclass(frozen=True, slots=True)
class PreheatRate:
    """Recovery rate learned for a room and the number of episodes behind it."""

    episodes: int
    rate_per_hour: float

    def lead_minutes(self, deficit: float) -> int:
        """Return the minutes needed to close the deficit, leaning towards being early."""
        if deficit <= 0:
            return 0

        return ceil(deficit / self.rate_per_hour * 60 * LEAD_TIME_MARGIN)


class RoomRecoveryTracker:
    """Follow one room from falling behind its target until it is back."""

    def __init__(self) -> None:
        self._started_at: Optional[float] = None
        self._start_shortfall: Optional[float] = None
        self._outside_temperatures: list[float] = []

    def update(self, boiler: BoilerObservation, room: RoomObservation, now: float) -> Optional[RecoveryEpisode]:
        """Return an episode once the room has recovered."""
        if not boiler.eligible or room.signals.window_open or room.signals.valve_alarm or room.shortfall is None:
            self._reset()
            return None

        if self._started_at is None:
            if room.shortfall >= RECOVERY_MINIMUM_SHORTFALL_CELSIUS:
                self._started_at, self._start_shortfall = now, room.shortfall

            return None

        if (now - self._started_at) > RECOVERY_MAXIMUM_SECONDS or room.shortfall > self._start_shortfall + RECOVERY_MAXIMUM_RISE_CELSIUS:
            self._reset()
            return None

        if boiler.outside_temperature is not None:
            self._outside_temperatures.append(boiler.outside_temperature)

        if room.shortfall > RECOVERY_FINISHED_SHORTFALL_CELSIUS:
            return None

        hours = (now - self._started_at) / 3600
        degrees = self._start_shortfall - room.shortfall
        outside_temperature = fmean(self._outside_temperatures) if self._outside_temperatures else None
        self._reset()

        if hours <= 0:
            return None

        return RecoveryEpisode(
            room_id=room.room_id,
            degrees=round(degrees, 2),
            rate_per_hour=round(degrees / hours, 3),
            outside_temperature=outside_temperature,
            date=dt_util.as_local(dt_util.utc_from_timestamp(now)).date().isoformat(),
        )

    def _reset(self) -> None:
        self._started_at = None
        self._start_shortfall = None
        self._outside_temperatures = []


class PreheatEvaluator:
    """Turn recovery episodes into a rate for a room and outside temperature."""

    @staticmethod
    def rate(episodes: list[RecoveryEpisode], outside_temperature: Optional[float]) -> Optional[PreheatRate]:
        if outside_temperature is not None:
            bands = PreheatEvaluator.bands(episodes)
            wanted = floor(outside_temperature / BAND_WIDTH_CELSIUS) * BAND_WIDTH_CELSIUS

            if bands:
                return bands[min(bands, key=lambda band: abs(band - wanted))]

        if len(episodes) < MINIMUM_EPISODES:
            return None

        return PreheatEvaluator._summarise(episodes)

    @staticmethod
    def bands(episodes: list[RecoveryEpisode]) -> dict[int, PreheatRate]:
        grouped: dict[int, list[RecoveryEpisode]] = {}

        for episode in episodes:
            if episode.band is not None:
                grouped.setdefault(episode.band, []).append(episode)

        return {
            band: PreheatEvaluator._summarise(items)
            for band, items in sorted(grouped.items())
            if len(items) >= MINIMUM_EPISODES
        }

    @staticmethod
    def _summarise(episodes: list[RecoveryEpisode]) -> PreheatRate:
        return PreheatRate(episodes=len(episodes), rate_per_hour=median(episode.rate_per_hour for episode in episodes))


class PreheatForecast:
    """Look up the outside temperature expected at a moment in the future."""

    @staticmethod
    async def async_temperature(hass: HomeAssistant, entity_ids: list[str], moment: datetime) -> Optional[float]:
        """Return the temperature from the first weather entity with a nearby forecast entry."""
        for entity_id in entity_ids:
            if not entity_id.startswith("weather."):
                continue

            try:
                response = await hass.services.async_call(
                    "weather", "get_forecasts", {"entity_id": entity_id, "type": "hourly"}, blocking=True, return_response=True
                )
            except Exception as exception:
                _LOGGER.debug("Unable to read the forecast of %s: %s", entity_id, exception)
                continue

            if (temperature := PreheatForecast._closest(response.get(entity_id, {}).get("forecast", []), moment)) is not None:
                return temperature

        return None

    @staticmethod
    def _closest(entries: list[dict], moment: datetime) -> Optional[float]:
        best_gap, best_temperature = None, None

        for entry in entries:
            if (entry_time := dt_util.parse_datetime(str(entry.get("datetime")))) is None or (temperature := float_value(entry.get("temperature"))) is None:
                continue

            gap = abs((entry_time - moment).total_seconds())

            if gap <= FORECAST_MAXIMUM_GAP_SECONDS and (best_gap is None or gap < best_gap):
                best_gap, best_temperature = gap, temperature

        return best_temperature


class PreheatMonitor:
    """Keep the recovery episodes of every room across restarts."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id
        self._store: Optional[Store] = None
        self._hass: Optional[HomeAssistant] = None
        self._episodes: Deque[RecoveryEpisode] = deque()
        self._trackers: dict[str, RoomRecoveryTracker] = {}

    async def async_added_to_hass(self, hass: HomeAssistant) -> None:
        """Restore the episodes from storage when the integration loads."""
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, f"sat.preheat.{self._entry_id}")

        if not (data := await self._store.async_load()):
            return

        self._episodes = deque(RecoveryEpisode(**episode) for episode in data.get("episodes", []))

        _LOGGER.debug("Restored %d recovery episodes.", len(self._episodes))

    async def async_save_data(self) -> None:
        """Persist the episodes immediately."""
        if self._store is not None:
            await self._store.async_save(self._data_to_save())

    def episodes(self, room_id: str) -> list[RecoveryEpisode]:
        """Return the completed recoveries of a room, oldest first."""
        return [episode for episode in list(self._episodes) if episode.room_id == room_id]

    def rate(self, room_id: str, outside_temperature: Optional[float]) -> Optional[PreheatRate]:
        """Return the rate to expect for a room at the given outside temperature."""
        return PreheatEvaluator.rate(self.episodes(room_id), outside_temperature)

    def observe(self, boiler: BoilerObservation, rooms: list[RoomObservation], now: float) -> None:
        """Process the current state of all rooms."""
        for room in rooms:
            tracker = self._trackers.setdefault(room.room_id, RoomRecoveryTracker())

            if (episode := tracker.update(boiler, room, now)) is not None:
                self._record(episode, now)

    def _record(self, episode: RecoveryEpisode, now: float) -> None:
        self._episodes.append(episode)

        today = dt_util.as_local(dt_util.utc_from_timestamp(now)).date()
        cutoff = (today - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()

        while self._episodes and self._episodes[0].date < cutoff:
            self._episodes.popleft()

        if self._hass is not None and self._store is not None:
            data = self._data_to_save()
            self._hass.loop.call_soon_threadsafe(self._store.async_delay_save, lambda: data)

    def _data_to_save(self) -> dict:
        return {"episodes": [asdict(episode) for episode in list(self._episodes)]}
