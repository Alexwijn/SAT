import logging
from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Deque, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from . import SatConfig
from .helpers import clamp
from .types import HeatingCurveRecommendation, HeatingSystem

_LOGGER = logging.getLogger(__name__)

RECOMMENDATION_WINDOW_SECONDS = 24 * 60 * 60
RECOMMENDATION_MINIMUM_SAMPLES = 180
RECOMMENDATION_MINIMUM_SHAPE = 4.0
RECOMMENDATION_COEFFICIENT_STEP = 0.1

MINIMUM_COEFFICIENT = 0.1
MAXIMUM_COEFFICIENT = 12.0

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY_SECONDS = 300


class HeatingCurve:
    """Compute and store heating curve targets based on outdoor temperature."""

    def __init__(self, heating_system: HeatingSystem, coefficient: float):
        self._value: Optional[float] = None
        self._coefficient: float = coefficient
        self._heating_system: HeatingSystem = heating_system

    @property
    def value(self) -> Optional[float]:
        return self._value

    @staticmethod
    def from_config(config: SatConfig):
        """Build a heating curve using values from the integration config."""
        return HeatingCurve(heating_system=config.heating_system, coefficient=config.pid.heating_curve_coefficient)

    @staticmethod
    def calculate(target_temperature: float, outside_temperature: float) -> float:
        """Return the unscaled curve value for the given target and outdoor temperatures."""
        return 4 * (target_temperature - 20) + 0.03 * (outside_temperature - 20) ** 2 - 0.4 * (outside_temperature - 20)

    def reset(self):
        """Clear the cached curve value."""
        self._value = None

    def update(self, target_temperature: float, outside_temperature: float) -> None:
        """Recalculate and store the scaled curve value for the current conditions."""
        heating_curve_value = self.calculate(target_temperature, outside_temperature)
        self._value = round(self._heating_system.base_offset + ((self._coefficient / 4) * heating_curve_value), 1)


@dataclass(frozen=True, slots=True)
class HeatingCurveAdviceSample:
    """Single observation of the correction the PID applies on top of the curve."""

    shape: float
    timestamp: float
    correction: float


@dataclass(frozen=True, slots=True)
class HeatingCurveAdvice:
    """Heating curve evaluation output."""

    recommendation: HeatingCurveRecommendation
    sample_count: int
    median_correction: Optional[float]
    suggested_coefficient: Optional[float]


class HeatingCurveAdvisor:
    """Recommend heating curve adjustments based on the sustained PID correction.

    A well-tuned curve makes the PID output match the curve value, so the
    correction the controller keeps applying on top of the curve is the
    direct measure of how far the coefficient is off. Since the curve value
    is base + (coefficient / 4) * shape, each sample implies a coefficient
    delta of 4 * correction / shape.
    """

    def __init__(self, coefficient: float) -> None:
        self._coefficient = coefficient
        self._store: Optional[Store] = None
        self._samples: Deque[HeatingCurveAdviceSample] = deque()

    async def async_added_to_hass(self, hass: HomeAssistant, entry_id: str) -> None:
        """Restore the recorded samples from storage when the integration loads."""
        self._store = Store(hass, STORAGE_VERSION, f"sat.curve_advisor.{entry_id}")

        if not (data := await self._store.async_load()):
            return

        if data.get("coefficient") != self._coefficient:
            return

        self._samples = deque(
            HeatingCurveAdviceSample(shape=sample["shape"], timestamp=sample["timestamp"], correction=sample["correction"])
            for sample in data.get("samples", [])
        )

        _LOGGER.debug("Restored %d heating curve advisor samples.", len(self._samples))

    def record(self, timestamp: float, correction: float, target_temperature: float, outside_temperature: float) -> None:
        """Record the correction the PID currently applies on top of the curve."""
        shape = HeatingCurve.calculate(target_temperature, outside_temperature)

        if shape < RECOMMENDATION_MINIMUM_SHAPE:
            return

        self._samples.append(HeatingCurveAdviceSample(shape=shape, timestamp=timestamp, correction=correction))

        while self._samples and self._samples[0].timestamp < (timestamp - RECOMMENDATION_WINDOW_SECONDS):
            self._samples.popleft()

        if self._store is not None:
            self._store.async_delay_save(self._data_to_save, STORAGE_SAVE_DELAY_SECONDS)

    def _data_to_save(self) -> dict:
        return {
            "coefficient": self._coefficient,
            "samples": [
                {"shape": sample.shape, "timestamp": sample.timestamp, "correction": sample.correction}
                for sample in self._samples
            ],
        }

    @property
    def advice(self) -> HeatingCurveAdvice:
        """Evaluate the recorded corrections into a recommendation."""
        if (sample_count := len(self._samples)) < RECOMMENDATION_MINIMUM_SAMPLES:
            return HeatingCurveAdvice(
                recommendation=HeatingCurveRecommendation.INSUFFICIENT_SAMPLES,
                sample_count=sample_count,
                median_correction=None,
                suggested_coefficient=None,
            )

        median_correction = float(median(sample.correction for sample in self._samples))
        median_delta = float(median(4 * sample.correction / sample.shape for sample in self._samples))

        suggested_coefficient = round(clamp(self._coefficient + median_delta, MINIMUM_COEFFICIENT, MAXIMUM_COEFFICIENT), 1)

        if suggested_coefficient >= self._coefficient + RECOMMENDATION_COEFFICIENT_STEP:
            recommendation = HeatingCurveRecommendation.INCREASE
        elif suggested_coefficient <= self._coefficient - RECOMMENDATION_COEFFICIENT_STEP:
            recommendation = HeatingCurveRecommendation.DECREASE
        else:
            recommendation = HeatingCurveRecommendation.HOLD

        return HeatingCurveAdvice(
            recommendation=recommendation,
            sample_count=sample_count,
            median_correction=round(median_correction, 1),
            suggested_coefficient=suggested_coefficient,
        )
