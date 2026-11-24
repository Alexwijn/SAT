from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import mean, median
from threading import Lock
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
        self._lock = Lock()

    @property
    def statistics(self) -> TemperatureStatistics:
        """Snapshot of rolling temperature statistics."""
        return TemperatureStatistics(window=self.window_statistics)

    @property
    def window_statistics(self) -> TemperatureWindowStatistics:
        """Snapshot of rolling window statistics."""
        samples = self._snapshot_samples()
        return TemperatureWindowStatistics(
            recent=self._window_snapshot(self._recent_samples(samples, RECENT_WINDOW_SECONDS)),
            daily=self._window_snapshot(self._recent_samples(samples, DAILY_WINDOW_SECONDS)),
        )

    def record(self, error: float, timestamp: float) -> None:
        """Record a new temperature error sample."""
        with self._lock:
            self._samples.append(TemperatureErrorSample(timestamp=timestamp, error=error))
            cutoff = timestamp - DAILY_WINDOW_SECONDS
            while self._samples and self._samples[0].timestamp < cutoff:
                self._samples.popleft()

    def _window_snapshot(self, samples: list[TemperatureErrorSample]) -> TemperatureWindowSnapshot:
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

    def median_error(self, window_seconds: int) -> Optional[float]:
        """Return the median error over the window."""
        if not (samples := self._recent_samples(self._snapshot_samples(), window_seconds)):
            return None

        return float(median(sample.error for sample in samples))

    def is_steady(self, window_seconds: int, maximum_range: float) -> bool:
        """Whether the error has covered the whole window without drifting more than the given range."""
        samples = self._recent_samples(self._snapshot_samples(), window_seconds)

        if len(samples) < 3 or (samples[-1].timestamp - samples[0].timestamp) < 0.8 * window_seconds:
            return False

        errors = [sample.error for sample in samples]
        return (max(errors) - min(errors)) <= maximum_range

    def _snapshot_samples(self) -> tuple[TemperatureErrorSample, ...]:
        with self._lock:
            return tuple(self._samples)

    @staticmethod
    def _recent_samples(samples: tuple[TemperatureErrorSample, ...], window_seconds: int) -> list[TemperatureErrorSample]:
        if not samples:
            return []

        cutoff = samples[-1].timestamp - window_seconds
        return [sample for sample in samples if sample.timestamp >= cutoff]

    @staticmethod
    def _in_band_fraction(errors: list[float]) -> float:
        if not errors:
            return 0.0

        in_band_count = sum(1 for error in errors if abs(error) <= DEADBAND)
        return in_band_count / len(errors)
