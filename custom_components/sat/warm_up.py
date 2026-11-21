"""Estimate how fast the house warms up while actively heating."""
from __future__ import annotations

from typing import Optional

from .helpers import float_value

WARM_UP_ALPHA = 0.3
WARM_UP_WINDOW_SECONDS = 2400.0
WARM_UP_MINIMUM_SPAN_SECONDS = 1800.0
WARM_UP_MINIMUM_ERROR_CELSIUS = 0.3


class WarmUpEstimator:
    """Learn the typical indoor warm-up rate (°C/h) from active heating periods."""

    def __init__(self) -> None:
        self._rate: Optional[float] = None
        self._samples: list[tuple[float, float]] = []

    @property
    def rate(self) -> Optional[float]:
        """Return the learned warm-up rate in °C/h."""
        return self._rate

    def restore(self, rate) -> None:
        """Restore a previously learned rate."""
        self._rate = float_value(rate)

    def update(self, active: bool, temperature: Optional[float], timestamp: float) -> Optional[float]:
        """Process a sample; only periods of uninterrupted active heating contribute."""
        if not active or temperature is None:
            self._samples = []
            return self._rate

        self._samples.append((timestamp, temperature))
        self._samples = [sample for sample in self._samples if (timestamp - sample[0]) <= WARM_UP_WINDOW_SECONDS]

        oldest_timestamp, oldest_temperature = self._samples[0]
        elapsed_seconds = timestamp - oldest_timestamp

        if elapsed_seconds < WARM_UP_MINIMUM_SPAN_SECONDS:
            return self._rate

        slope = (temperature - oldest_temperature) * 3600 / elapsed_seconds
        self._samples = [(timestamp, temperature)]

        if slope <= 0:
            return self._rate

        if self._rate is None:
            self._rate = round(slope, 2)
        else:
            self._rate = round(WARM_UP_ALPHA * slope + (1 - WARM_UP_ALPHA) * self._rate, 2)

        return self._rate
