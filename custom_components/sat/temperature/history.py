from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import mean, median
from typing import Deque, Optional

from ..const import DEADBAND

RECENT_WINDOW_SECONDS: int = 4 * 60 * 60
DAILY_WINDOW_SECONDS: int = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class TemperatureErrorSample:
    timestamp: float
    error: float


@dataclass(frozen=True, slots=True)
class TemperatureWindowSnapshot:
    sample_count: int
    median_error: Optional[float]
    mean_error: Optional[float]
    mean_abs_error: Optional[float]
    in_band_fraction: float


@dataclass(frozen=True, slots=True)
class TemperatureWindowStatistics:
    recent: TemperatureWindowSnapshot
    daily: TemperatureWindowSnapshot


@dataclass(frozen=True, slots=True)
class TemperatureStatistics:
    window: TemperatureWindowStatistics


class TemperatureHistory:
    """Rolling history of room temperature error samples."""

    def __init__(self) -> None:
        self._samples: Deque[TemperatureErrorSample] = deque()

    @property
    def statistics(self) -> TemperatureStatistics:
        """Snapshot of rolling temperature statistics."""
        return TemperatureStatistics(window=self.window_statistics)

    @property
    def window_statistics(self) -> TemperatureWindowStatistics:
        """Snapshot of rolling window statistics."""
        return TemperatureWindowStatistics(
            recent=self._window_snapshot(RECENT_WINDOW_SECONDS),
            daily=self._window_snapshot(DAILY_WINDOW_SECONDS),
        )

    def record(self, error: float, timestamp: float) -> None:
        """Record a new temperature error sample."""
        self._samples.append(TemperatureErrorSample(timestamp=timestamp, error=error))
        self._prune(timestamp)

    def _window_snapshot(self, window_seconds: int) -> TemperatureWindowSnapshot:
        samples = self._recent_samples(window_seconds)
        errors = [sample.error for sample in samples]

        if not errors:
            return TemperatureWindowSnapshot(
                sample_count=0,
                median_error=None,
                mean_error=None,
                mean_abs_error=None,
                in_band_fraction=0.0,
            )

        return TemperatureWindowSnapshot(
            sample_count=len(errors),
            median_error=float(median(errors)),
            mean_error=float(mean(errors)),
            mean_abs_error=float(mean(abs(error) for error in errors)),
            in_band_fraction=self._in_band_fraction(errors),
        )

    def _recent_samples(self, window_seconds: int) -> list[TemperatureErrorSample]:
        if (now := self._current_time_hint()) is None:
            return []

        cutoff = now - window_seconds
        return [sample for sample in self._samples if sample.timestamp >= cutoff]

    def _prune(self, now: float) -> None:
        while self._samples and self._samples[0].timestamp < (now - DAILY_WINDOW_SECONDS):
            self._samples.popleft()

    def _current_time_hint(self) -> Optional[float]:
        if not self._samples:
            return None

        return self._samples[-1].timestamp

    @staticmethod
    def _in_band_fraction(errors: list[float]) -> float:
        if not errors:
            return 0.0

        in_band_count = sum(1 for error in errors if abs(error) <= DEADBAND)
        return in_band_count / len(errors)
