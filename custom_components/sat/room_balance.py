"""Daily per-room history of how well each room is supplied with heat."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from statistics import fmean, median
from typing import Deque, Optional

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .helpers import float_value

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_SAVE_INTERVAL_SECONDS = 300.0

MAIN_ROOM_ID = "main"
HISTORY_RETENTION_DAYS = 90
SAMPLE_INTERVAL_SECONDS = 300.0

WARM_UP_MINIMUM_SPAN_SECONDS = 1800.0
WARM_UP_MINIMUM_SHORTFALL_CELSIUS = 0.3

HEAT_RISE_MINIMUM_SPAN_SECONDS = 600.0

UNIQUE_ID_WINDOW_OPEN = "window_open"
UNIQUE_ID_VALVE_ALARM = "valve_alarm"
UNIQUE_ID_DEVICE_TEMPERATURE = "device_temperature"


@dataclass(frozen=True, slots=True)
class RoomSignalReading:
    """Optional extras reported by the device that controls a room."""

    window_open: bool = False
    valve_alarm: bool = False
    device_temperature: Optional[float] = None


class RoomSignals:
    """Find optional signals through the device registry, without relying on a device model."""

    @staticmethod
    def read(hass: HomeAssistant, entity_id: str) -> RoomSignalReading:
        registry = er.async_get(hass)

        if (entry := registry.async_get(entity_id)) is None or entry.device_id is None:
            return RoomSignalReading()

        entries = er.async_entries_for_device(registry, entry.device_id)

        return RoomSignalReading(
            window_open=any(RoomSignals._is_on(hass, item) for item in entries if RoomSignals._is_window(item)),
            valve_alarm=any(RoomSignals._is_on(hass, item) for item in entries if RoomSignals._is_valve_alarm(item)),
            device_temperature=RoomSignals._device_temperature(hass, entries),
        )

    @staticmethod
    def _is_on(hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
        return (state := hass.states.get(entry.entity_id)) is not None and state.state == STATE_ON

    @staticmethod
    def _is_window(entry: er.RegistryEntry) -> bool:
        if entry.domain != "binary_sensor":
            return False

        return (entry.device_class or entry.original_device_class) == BinarySensorDeviceClass.WINDOW or UNIQUE_ID_WINDOW_OPEN in entry.unique_id

    @staticmethod
    def _is_valve_alarm(entry: er.RegistryEntry) -> bool:
        return entry.domain == "binary_sensor" and UNIQUE_ID_VALVE_ALARM in entry.unique_id

    @staticmethod
    def _device_temperature(hass: HomeAssistant, entries: list[er.RegistryEntry]) -> Optional[float]:
        candidates = [
            item for item in entries
            if item.domain == "sensor" and (item.device_class or item.original_device_class) == SensorDeviceClass.TEMPERATURE
        ]

        named = [item for item in candidates if UNIQUE_ID_DEVICE_TEMPERATURE in item.unique_id]
        chosen = named if named else candidates

        if len(chosen) != 1 or (state := hass.states.get(chosen[0].entity_id)) is None:
            return None

        return float_value(state.state)


@dataclass(frozen=True, slots=True)
class BoilerObservation:
    """State of the heating system at the moment the rooms were observed."""

    eligible: bool
    heating: bool
    flame_active: bool
    flow_temperature: Optional[float]


@dataclass(frozen=True, slots=True)
class RoomObservation:
    """State of a single room at a moment in time."""

    room_id: str
    steady: bool = False
    calling: bool = False
    shape: Optional[float] = None
    shortfall: Optional[float] = None
    temperature: Optional[float] = None
    valve_position: Optional[float] = None
    implied_coefficient: Optional[float] = None
    signals: RoomSignalReading = RoomSignalReading()


@dataclass(frozen=True, slots=True)
class RoomBalanceDay:
    """Summary of one room over a single completed day."""

    date: str
    room_id: str
    sample_count: int = 0
    calling_fraction: float = 0.0
    shape: Optional[float] = None
    shortfall: Optional[float] = None
    heat_rise: Optional[float] = None
    valve_position: Optional[float] = None
    warm_up_response: Optional[float] = None
    warm_up_episodes: int = 0
    implied_coefficient: Optional[float] = None

    @property
    def ordinal(self) -> int:
        return date.fromisoformat(self.date).toordinal()


@dataclass(slots=True)
class RoomBalanceAccumulator:
    """Raw values collected for a room during the current day."""

    calling_samples: int = 0
    shapes: list[float] = field(default_factory=list)
    shortfalls: list[float] = field(default_factory=list)
    heat_rises: list[float] = field(default_factory=list)
    valve_positions: list[float] = field(default_factory=list)
    warm_up_responses: list[float] = field(default_factory=list)
    implied_coefficients: list[float] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return bool(self.implied_coefficients or self.warm_up_responses or self.heat_rises)

    def to_day(self, day: str, room_id: str) -> RoomBalanceDay:
        sample_count = len(self.implied_coefficients)

        return RoomBalanceDay(
            date=day,
            room_id=room_id,
            sample_count=sample_count,
            warm_up_episodes=len(self.warm_up_responses),
            calling_fraction=self.calling_samples / sample_count if sample_count else 0.0,
            shape=median(self.shapes) if self.shapes else None,
            shortfall=median(self.shortfalls) if self.shortfalls else None,
            heat_rise=median(self.heat_rises) if self.heat_rises else None,
            valve_position=median(self.valve_positions) if self.valve_positions else None,
            warm_up_response=median(self.warm_up_responses) if self.warm_up_responses else None,
            implied_coefficient=median(self.implied_coefficients) if self.implied_coefficients else None,
        )


@dataclass(frozen=True, slots=True)
class WarmUpPoint:
    """Temperatures seen during a warm-up episode."""

    timestamp: float
    temperature: float
    flow_temperature: float


class RoomWarmUpTracker:
    """Measure how fast a room heats for the flow temperature it receives."""

    def __init__(self) -> None:
        self._points: list[WarmUpPoint] = []

    def update(self, active: bool, point: Optional[WarmUpPoint]) -> Optional[float]:
        """Return the response in °C per hour per °C of flow excess once an episode completes."""
        if not active or point is None:
            self._points = []
            return None

        self._points.append(point)
        first = self._points[0]
        elapsed = point.timestamp - first.timestamp

        if elapsed < WARM_UP_MINIMUM_SPAN_SECONDS:
            return None

        excess = fmean(item.flow_temperature - item.temperature for item in self._points)
        rate = (point.temperature - first.temperature) * 3600 / elapsed
        self._points = [point]

        if rate <= 0 or excess <= 0:
            return None

        return rate / excess


class RoomHeatRiseTracker:
    """Measure how much the device in a room warms up while the boiler fires."""

    def __init__(self) -> None:
        self._started_at: Optional[float] = None
        self._lowest: Optional[float] = None
        self._highest: Optional[float] = None

    def update(self, flame_active: bool, temperature: Optional[float], now: float) -> Optional[float]:
        """Return the rise in °C when a long enough flame period ends."""
        if flame_active:
            if self._started_at is None:
                self._started_at = now

            if temperature is not None:
                self._lowest = temperature if self._lowest is None else min(self._lowest, temperature)
                self._highest = temperature if self._highest is None else max(self._highest, temperature)

            return None

        started_at, lowest, highest = self._started_at, self._lowest, self._highest
        self._started_at = self._lowest = self._highest = None

        if started_at is None or lowest is None or (now - started_at) < HEAT_RISE_MINIMUM_SPAN_SECONDS:
            return None

        return highest - lowest


class RoomBalanceMonitor:
    """Keep a daily history per room that survives restarts."""

    def __init__(self, entry_id: str, room_ids: list[str]) -> None:
        self._entry_id = entry_id
        self._room_ids: set[str] = {*room_ids, MAIN_ROOM_ID}

        self._store: Optional[Store] = None
        self._hass: Optional[HomeAssistant] = None
        self._saved_at: Optional[float] = None
        self._sampled_at: Optional[float] = None

        self._current_date: Optional[str] = None
        self._days: Deque[RoomBalanceDay] = deque()
        self._current: dict[str, RoomBalanceAccumulator] = {}

        self._warm_up: dict[str, RoomWarmUpTracker] = {}
        self._heat_rise: dict[str, RoomHeatRiseTracker] = {}

    async def async_added_to_hass(self, hass: HomeAssistant) -> None:
        """Restore the history from storage when the integration loads."""
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, f"sat.room_balance.{self._entry_id}")

        if not (data := await self._store.async_load()):
            return

        self._days = deque(day for day in (RoomBalanceDay(**item) for item in data.get("days", [])) if day.room_id in self._room_ids)
        self._current_date = data.get("current_date")
        self._current = {
            room_id: RoomBalanceAccumulator(**values)
            for room_id, values in data.get("current", {}).items()
            if room_id in self._room_ids
        }

        _LOGGER.debug("Restored %d room balance days.", len(self._days))

    async def async_save_data(self) -> None:
        """Persist the history immediately."""
        if self._store is not None:
            await self._store.async_save(self._data_to_save())

    @property
    def days(self) -> list[RoomBalanceDay]:
        """Completed days of every room, oldest first."""
        return list(self._days)

    def observe(self, boiler: BoilerObservation, rooms: list[RoomObservation], now: float) -> None:
        """Process the current state of all rooms."""
        self._roll_over(now)

        for room in rooms:
            self._track_warm_up(boiler, room, now)
            self._track_heat_rise(boiler, room, now)

        if not boiler.eligible or not boiler.heating:
            return

        if self._sampled_at is not None and (now - self._sampled_at) < SAMPLE_INTERVAL_SECONDS:
            return

        self._sampled_at = now

        for room in rooms:
            self._record_steady_sample(room)

        self._schedule_save(now)

    def _record_steady_sample(self, room: RoomObservation) -> None:
        if not room.steady or room.implied_coefficient is None or room.shortfall is None or self._is_excluded(room):
            return

        accumulator = self._current.setdefault(room.room_id, RoomBalanceAccumulator())
        accumulator.implied_coefficients.append(room.implied_coefficient)
        accumulator.shortfalls.append(room.shortfall)
        accumulator.calling_samples += 1 if room.calling else 0

        if room.shape is not None:
            accumulator.shapes.append(room.shape)

        if room.valve_position is not None:
            accumulator.valve_positions.append(room.valve_position)

    def _track_warm_up(self, boiler: BoilerObservation, room: RoomObservation, now: float) -> None:
        tracker = self._warm_up.setdefault(room.room_id, RoomWarmUpTracker())

        active = (
            boiler.eligible
            and boiler.flame_active
            and not self._is_excluded(room)
            and room.shortfall is not None
            and room.shortfall >= WARM_UP_MINIMUM_SHORTFALL_CELSIUS
        )

        point = None
        if room.temperature is not None and boiler.flow_temperature is not None:
            point = WarmUpPoint(timestamp=now, temperature=room.temperature, flow_temperature=boiler.flow_temperature)

        if (response := tracker.update(active, point)) is not None:
            self._current.setdefault(room.room_id, RoomBalanceAccumulator()).warm_up_responses.append(response)

    def _track_heat_rise(self, boiler: BoilerObservation, room: RoomObservation, now: float) -> None:
        tracker = self._heat_rise.setdefault(room.room_id, RoomHeatRiseTracker())

        if (rise := tracker.update(boiler.flame_active and boiler.eligible, room.signals.device_temperature, now)) is not None:
            self._current.setdefault(room.room_id, RoomBalanceAccumulator()).heat_rises.append(rise)

    @staticmethod
    def _is_excluded(room: RoomObservation) -> bool:
        return room.signals.window_open or room.signals.valve_alarm

    def _roll_over(self, now: float) -> None:
        today = dt_util.as_local(dt_util.utc_from_timestamp(now)).date()

        if self._current_date is None:
            self._current_date = today.isoformat()
            return

        if self._current_date == today.isoformat():
            return

        for room_id, accumulator in self._current.items():
            if accumulator.has_data:
                self._days.append(accumulator.to_day(self._current_date, room_id))

        self._current = {}
        self._current_date = today.isoformat()

        cutoff = (today - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()
        while self._days and self._days[0].date < cutoff:
            self._days.popleft()

        self._saved_at = None
        self._schedule_save(now)

    def _schedule_save(self, now: float) -> None:
        if self._hass is None or self._store is None:
            return

        if self._saved_at is not None and (now - self._saved_at) < STORAGE_SAVE_INTERVAL_SECONDS:
            return

        self._saved_at = now
        data = self._data_to_save()
        self._hass.loop.call_soon_threadsafe(self._store.async_delay_save, lambda: data)

    def _data_to_save(self) -> dict:
        return {
            "current_date": self._current_date,
            "days": [asdict(day) for day in list(self._days)],
            "current": {room_id: asdict(accumulator) for room_id, accumulator in list(self._current.items())},
        }
