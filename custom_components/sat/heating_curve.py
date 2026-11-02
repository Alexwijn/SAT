import logging
from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Deque, Mapping, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import SatConfig
from .helpers import clamp, timestamp, weighted_median
from .types import HeatingCurveConfidence, HeatingCurveRecommendation, HeatingSystem
from .wind import WindReading

_LOGGER = logging.getLogger(__name__)

RECOMMENDATION_WINDOW_SECONDS = 7 * 24 * 60 * 60
RECOMMENDATION_SAMPLE_INTERVAL_SECONDS = 60.0
RECOMMENDATION_MINIMUM_DAYS = 2
RECOMMENDATION_HIGH_CONFIDENCE_DAYS = 3
RECOMMENDATION_MINIMUM_SAMPLES = 180
RECOMMENDATION_MINIMUM_DAILY_SAMPLES = 60
RECOMMENDATION_MINIMUM_SHAPE = 4.0
RECOMMENDATION_MAXIMUM_ERROR_CELSIUS = 1.0
RECOMMENDATION_STEADY_WINDOW_SECONDS = 1800
RECOMMENDATION_STEADY_RANGE_CELSIUS = 0.4
RECOMMENDATION_REQUEST_TOLERANCE_CELSIUS = 0.5
RECOMMENDATION_MAXIMUM_SUN_ELEVATION = 0.0
RECOMMENDATION_RELIABLE_SHAPE = 8.0
RECOMMENDATION_HOLD_TOLERANCE = 0.2
RECOMMENDATION_MINIMUM_STEP_LIMIT = 0.5
RECOMMENDATION_RELATIVE_STEP_LIMIT = 0.5
RECOMMENDATION_AGREEMENT_THRESHOLD = 2 / 3

MINIMUM_COEFFICIENT = 0.1
MAXIMUM_COEFFICIENT = 12.0

STORAGE_VERSION = 1
STORAGE_SAVE_INTERVAL_SECONDS = 300.0


class HeatingCurve:
    """Compute and store heating curve targets based on outdoor temperature."""

    def __init__(self, heating_system: HeatingSystem, coefficient: float):
        self._value: Optional[float] = None
        self._shape: Optional[float] = None
        self._coefficient: float = coefficient
        self._heating_system: HeatingSystem = heating_system

    @property
    def value(self) -> Optional[float]:
        return self._value

    @property
    def shape(self) -> Optional[float]:
        """Return the unscaled curve value the current value was derived from."""
        return self._shape

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
        self._shape = None

    def update(self, target_temperature: float, outside_temperature: float) -> None:
        """Recalculate and store the scaled curve value for the current conditions."""
        self._shape = self.calculate(target_temperature, outside_temperature)
        self._value = round(self._heating_system.base_offset + ((self._coefficient / 4) * self._shape), 1)


@dataclass(frozen=True, slots=True)
class HeatingCurveAdviceSample:
    """Single observation of the sustained correction the PID applies on top of the curve."""

    shape: float
    timestamp: float
    correction: float
    wind: Optional[WindReading] = None

    @property
    def coefficient_delta(self) -> float:
        """Return the coefficient change that would absorb this correction into the curve."""
        return 4 * self.correction / self.shape


@dataclass(frozen=True, slots=True)
class HeatingCurveDailyEstimate:
    """Coefficient delta implied by the samples recorded on a single day."""

    sample_count: int
    coefficient_delta: float
    wind_speed: Optional[float]


@dataclass(frozen=True, slots=True)
class HeatingCurveAdvice:
    """Heating curve evaluation output."""

    recommendation: HeatingCurveRecommendation
    confidence: Optional[HeatingCurveConfidence]
    sample_count: int
    sample_days: int
    median_correction: Optional[float]
    estimated_coefficient: Optional[float]
    suggested_coefficient: Optional[float]
    daily_coefficients: Mapping[str, float]
    daily_wind_speeds: Mapping[str, float]


class HeatingCurveAdvisor:
    """Recommend heating curve adjustments based on the sustained PID correction.

    A well-tuned curve makes the PID output match the curve value, so the
    correction the controller keeps applying on top of the curve is the
    direct measure of how far the coefficient is off. Since the curve value
    is base + (coefficient / 4) * shape, each sample implies a coefficient
    delta of 4 * correction / shape.

    Samples are grouped per day so that a single unusual day, such as a storm
    or a party, cannot dominate the outcome: every qualifying day yields one
    estimate and the recommendation follows the median of those estimates.
    Within a day the samples are weighted by shape, because the same
    correction says a lot more about the coefficient in cold weather than
    in mild weather.
    """

    def __init__(self, coefficient: float) -> None:
        self._coefficient = coefficient
        self._store: Optional[Store] = None
        self._hass: Optional[HomeAssistant] = None
        self._saved_at: Optional[float] = None
        self._advice: Optional[HeatingCurveAdvice] = None
        self._samples: Deque[HeatingCurveAdviceSample] = deque()

    async def async_added_to_hass(self, hass: HomeAssistant, entry_id: str) -> None:
        """Restore the recorded samples from storage when the integration loads."""
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, f"sat.curve_advisor.{entry_id}")

        if not (data := await self._store.async_load()):
            return

        if data.get("coefficient") != self._coefficient:
            return

        self._samples = deque(self._sample_from_data(sample) for sample in data.get("samples", []))

        self._prune(timestamp())
        self._advice = None

        _LOGGER.debug("Restored %d heating curve advisor samples.", len(self._samples))

    def record(self, sample: HeatingCurveAdviceSample) -> None:
        """Record the correction the PID currently applies on top of the curve."""
        if sample.shape < RECOMMENDATION_MINIMUM_SHAPE:
            return

        if self._samples and (sample.timestamp - self._samples[-1].timestamp) < RECOMMENDATION_SAMPLE_INTERVAL_SECONDS:
            return

        self._samples.append(sample)
        self._prune(sample.timestamp)
        self._advice = None

        self._schedule_save(sample.timestamp)

    def implied_coefficient(self, sample: Optional[HeatingCurveAdviceSample]) -> Optional[float]:
        """Return the coefficient that would have absorbed the correction of this sample."""
        if sample is None or sample.shape < RECOMMENDATION_MINIMUM_SHAPE:
            return None

        return round(clamp(self._coefficient + sample.coefficient_delta, MINIMUM_COEFFICIENT, MAXIMUM_COEFFICIENT), 2)

    def _prune(self, now: float) -> None:
        while self._samples and self._samples[0].timestamp < (now - RECOMMENDATION_WINDOW_SECONDS):
            self._samples.popleft()

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
            "coefficient": self._coefficient,
            "samples": [self._sample_to_data(sample) for sample in list(self._samples)],
        }

    @staticmethod
    def _sample_to_data(sample: HeatingCurveAdviceSample) -> dict:
        data = {"shape": sample.shape, "timestamp": sample.timestamp, "correction": sample.correction}

        if sample.wind is not None:
            data.update({"wind_speed": sample.wind.speed, "wind_entity_id": sample.wind.entity_id})

        return data

    @staticmethod
    def _sample_from_data(data: dict) -> HeatingCurveAdviceSample:
        wind = WindReading(speed=data["wind_speed"], entity_id=data["wind_entity_id"]) if data.get("wind_speed") is not None else None

        return HeatingCurveAdviceSample(shape=data["shape"], timestamp=data["timestamp"], correction=data["correction"], wind=wind)

    @property
    def advice(self) -> HeatingCurveAdvice:
        """Evaluate the recorded corrections into a recommendation."""
        if self._advice is None:
            self._advice = self._evaluate(list(self._samples))

        return self._advice

    def _evaluate(self, samples: list[HeatingCurveAdviceSample]) -> HeatingCurveAdvice:
        estimates = {
            day: estimate
            for day, estimate in self._daily_estimates(samples).items()
            if estimate.sample_count >= RECOMMENDATION_MINIMUM_DAILY_SAMPLES
        }

        if not estimates:
            return self._insufficient(samples, estimates, None)

        coefficient_delta = float(median(estimate.coefficient_delta for estimate in estimates.values()))
        estimated_coefficient = self._coefficient_for(coefficient_delta)
        qualifying_sample_count = sum(estimate.sample_count for estimate in estimates.values())

        if len(estimates) < RECOMMENDATION_MINIMUM_DAYS or qualifying_sample_count < RECOMMENDATION_MINIMUM_SAMPLES:
            return self._insufficient(samples, estimates, estimated_coefficient)

        recommendation = self._recommendation_for(coefficient_delta)
        agreeing_days = sum(1 for estimate in estimates.values() if self._recommendation_for(estimate.coefficient_delta) is recommendation)
        reliable = float(median(sample.shape for sample in samples)) >= RECOMMENDATION_RELIABLE_SHAPE

        return HeatingCurveAdvice(
            recommendation=recommendation,
            confidence=self._confidence_for(len(estimates), agreeing_days / len(estimates), reliable),
            sample_count=len(samples),
            sample_days=len(estimates),
            median_correction=self._median_correction(samples),
            estimated_coefficient=estimated_coefficient,
            suggested_coefficient=self._suggested_for(recommendation, estimated_coefficient),
            daily_coefficients=self._daily_coefficients(estimates),
            daily_wind_speeds=self._daily_wind_speeds(estimates),
        )

    def _insufficient(self, samples: list[HeatingCurveAdviceSample], estimates: Mapping[str, HeatingCurveDailyEstimate], estimated_coefficient: Optional[float]) -> HeatingCurveAdvice:
        return HeatingCurveAdvice(
            recommendation=HeatingCurveRecommendation.INSUFFICIENT_SAMPLES,
            confidence=None,
            sample_count=len(samples),
            sample_days=len(estimates),
            median_correction=self._median_correction(samples),
            estimated_coefficient=estimated_coefficient,
            suggested_coefficient=None,
            daily_coefficients=self._daily_coefficients(estimates),
            daily_wind_speeds=self._daily_wind_speeds(estimates),
        )

    @staticmethod
    def _median_correction(samples: list[HeatingCurveAdviceSample]) -> Optional[float]:
        if not samples:
            return None

        return round(float(median(sample.correction for sample in samples)), 1)

    def _daily_coefficients(self, estimates: Mapping[str, HeatingCurveDailyEstimate]) -> dict[str, float]:
        return {day: self._coefficient_for(estimate.coefficient_delta) for day, estimate in estimates.items()}

    @staticmethod
    def _daily_wind_speeds(estimates: Mapping[str, HeatingCurveDailyEstimate]) -> dict[str, float]:
        return {day: estimate.wind_speed for day, estimate in estimates.items() if estimate.wind_speed is not None}

    @staticmethod
    def _daily_estimates(samples: list[HeatingCurveAdviceSample]) -> dict[str, HeatingCurveDailyEstimate]:
        grouped: dict[str, list[HeatingCurveAdviceSample]] = {}

        for sample in samples:
            day = dt_util.as_local(dt_util.utc_from_timestamp(sample.timestamp)).date().isoformat()
            grouped.setdefault(day, []).append(sample)

        return {
            day: HeatingCurveDailyEstimate(
                sample_count=len(day_samples),
                coefficient_delta=weighted_median(
                    [sample.coefficient_delta for sample in day_samples],
                    [sample.shape for sample in day_samples],
                ),
                wind_speed=HeatingCurveAdvisor._median_wind_speed(day_samples),
            )
            for day, day_samples in sorted(grouped.items())
        }

    @staticmethod
    def _median_wind_speed(samples: list[HeatingCurveAdviceSample]) -> Optional[float]:
        if not (speeds := [sample.wind.speed for sample in samples if sample.wind is not None]):
            return None

        return round(float(median(speeds)), 1)

    def _coefficient_for(self, coefficient_delta: float) -> float:
        return round(clamp(self._coefficient + coefficient_delta, MINIMUM_COEFFICIENT, MAXIMUM_COEFFICIENT), 1)

    @staticmethod
    def _recommendation_for(coefficient_delta: float) -> HeatingCurveRecommendation:
        if coefficient_delta >= RECOMMENDATION_HOLD_TOLERANCE:
            return HeatingCurveRecommendation.INCREASE

        if coefficient_delta <= -RECOMMENDATION_HOLD_TOLERANCE:
            return HeatingCurveRecommendation.DECREASE

        return HeatingCurveRecommendation.HOLD

    @staticmethod
    def _confidence_for(days: int, agreement: float, reliable: bool) -> HeatingCurveConfidence:
        """Mild weather leaves little curve to measure against, so it never earns more than low confidence."""
        if not reliable or agreement < RECOMMENDATION_AGREEMENT_THRESHOLD:
            return HeatingCurveConfidence.LOW

        if days >= RECOMMENDATION_HIGH_CONFIDENCE_DAYS and agreement >= 1.0:
            return HeatingCurveConfidence.HIGH

        return HeatingCurveConfidence.MEDIUM

    def _suggested_for(self, recommendation: HeatingCurveRecommendation, estimated_coefficient: float) -> float:
        if recommendation is HeatingCurveRecommendation.HOLD:
            return self._coefficient

        step_limit = max(RECOMMENDATION_MINIMUM_STEP_LIMIT, RECOMMENDATION_RELATIVE_STEP_LIMIT * self._coefficient)
        step = clamp(estimated_coefficient - self._coefficient, -step_limit, step_limit)

        return self._coefficient_for(step)
