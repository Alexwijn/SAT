from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .const import *
from ..boiler import BoilerControlIntent


@dataclass(slots=True)
class RegimeState:
    key: RegimeKey
    minimum_setpoint: float

    stable_cycles: int = 0
    completed_cycles: int = 0
    last_seen: Optional[datetime] = None
    last_increase_at: Optional[datetime] = None

    increase_window_total: float = 0.0
    increase_window_start: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class RegimeKey:
    """Value object for regime bucketing."""

    setpoint_band: int
    outside_band: str
    delta_band: str

    def to_storage(self) -> str:
        return f"{self.setpoint_band}:{self.outside_band}:{self.delta_band}"

    @staticmethod
    def from_storage(value: Optional[str]) -> Optional["RegimeKey"]:
        if not value:
            return None

        parts = value.split(":")
        if len(parts) < 3:
            return None

        try:
            setpoint_band = int(parts[0])
        except (TypeError, ValueError):
            return None

        outside_band = parts[1]
        delta_band = parts[2]

        return RegimeKey(setpoint_band=setpoint_band, outside_band=outside_band, delta_band=delta_band)


@dataclass(slots=True)
class RegimeBucketizer:
    """Stateful bucketizer with hysteresis for regime keys."""

    previous_setpoint_band: Optional[int] = None
    previous_delta_bucket: Optional[str] = None
    previous_outside_temperature_bucket: Optional[str] = None

    def make_key(self, boiler_control_intent: BoilerControlIntent, flow_setpoint_error: Optional[float], outside_temperature: Optional[float]) -> RegimeKey:
        setpoint_band = self._bucket_setpoint_band_with_hysteresis(boiler_control_intent)
        delta_bucket = self._bucket_delta_with_hysteresis(flow_setpoint_error)
        outside_band = self._bucket_outside_temperature_with_hysteresis(outside_temperature)

        return RegimeKey(setpoint_band=setpoint_band, outside_band=outside_band, delta_band=delta_bucket)

    def _bucket_setpoint_band_with_hysteresis(self, boiler_control_intent: BoilerControlIntent) -> int:
        raw_band = int((boiler_control_intent.setpoint + (REGIME_BAND_WIDTH / 2.0)) // REGIME_BAND_WIDTH)

        previous_band = self.previous_setpoint_band
        if previous_band is None:
            self.previous_setpoint_band = raw_band
            return raw_band

        # Thresholds
        margin = REGIME_BAND_WIDTH * 0.25
        previous_center = previous_band * REGIME_BAND_WIDTH
        upper_boundary = previous_center + (REGIME_BAND_WIDTH / 2.0) + margin
        lower_boundary = previous_center - (REGIME_BAND_WIDTH / 2.0) - margin

        band = previous_band
        if boiler_control_intent.setpoint >= upper_boundary:
            band = raw_band
        elif boiler_control_intent.setpoint <= lower_boundary:
            band = raw_band

        self.previous_setpoint_band = band
        return band

    def _bucket_outside_temperature_with_hysteresis(self, outside_temperature: Optional[float]) -> str:
        previous = self.previous_outside_temperature_bucket

        if outside_temperature is None:
            return previous or OUTSIDE_BAND_UNKNOWN

        # Thresholds
        def initial_bucket(value: float) -> str:
            if value < OUTSIDE_TEMP_FREEZING_THRESHOLD:
                return OUTSIDE_BAND_FREEZING

            if value < OUTSIDE_TEMP_COLD_THRESHOLD:
                return OUTSIDE_BAND_COLD

            if value < OUTSIDE_TEMP_MILD_THRESHOLD:
                return OUTSIDE_BAND_MILD

            return OUTSIDE_BAND_WARM

        if previous is None:
            bucket = initial_bucket(outside_temperature)
            self.previous_outside_temperature_bucket = bucket
            return bucket

        if previous == OUTSIDE_BAND_FREEZING:
            if outside_temperature >= OUTSIDE_TEMP_FREEZING_THRESHOLD + OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_COLD

        elif previous == OUTSIDE_BAND_COLD:
            if outside_temperature < OUTSIDE_TEMP_FREEZING_THRESHOLD - OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_FREEZING

            elif outside_temperature >= OUTSIDE_TEMP_COLD_THRESHOLD + OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_MILD

        elif previous == OUTSIDE_BAND_MILD:
            if outside_temperature < OUTSIDE_TEMP_COLD_THRESHOLD - OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_COLD

            elif outside_temperature >= OUTSIDE_TEMP_MILD_THRESHOLD + OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_WARM

        elif previous == OUTSIDE_BAND_WARM:
            if outside_temperature < OUTSIDE_TEMP_MILD_THRESHOLD - OUTSIDE_TEMP_MARGIN:
                previous = OUTSIDE_BAND_MILD

        self.previous_outside_temperature_bucket = previous
        return previous

    def _bucket_delta_with_hysteresis(self, delta: Optional[float]) -> str:
        previous = self.previous_delta_bucket

        if delta is None:
            return previous or DELTA_BAND_UNKNOWN

        # Thresholds
        def raw_bucket(value: float) -> str:
            if value < DELTA_BAND_THRESHOLDS.low:
                return DELTA_BAND_VLOW
            if value < DELTA_BAND_THRESHOLDS.med:
                return DELTA_BAND_LOW
            if value < DELTA_BAND_THRESHOLDS.high:
                return DELTA_BAND_MED
            return DELTA_BAND_HIGH

        if previous is None:
            bucket = raw_bucket(delta)
            self.previous_delta_bucket = bucket
            return bucket

        if previous == DELTA_BAND_VLOW and delta >= DELTA_BAND_THRESHOLDS.low + DELTA_BAND_MARGIN:
            previous = DELTA_BAND_LOW
        elif previous == DELTA_BAND_LOW:
            if delta < DELTA_BAND_THRESHOLDS.low - DELTA_BAND_MARGIN:
                previous = DELTA_BAND_VLOW
            elif delta >= DELTA_BAND_THRESHOLDS.med + DELTA_BAND_MARGIN:
                previous = DELTA_BAND_MED
        elif previous == DELTA_BAND_MED:
            if delta < DELTA_BAND_THRESHOLDS.med - DELTA_BAND_MARGIN:
                previous = DELTA_BAND_LOW
            elif delta >= DELTA_BAND_THRESHOLDS.high + DELTA_BAND_MARGIN:
                previous = DELTA_BAND_HIGH
        elif previous == DELTA_BAND_HIGH and delta < DELTA_BAND_THRESHOLDS.high - DELTA_BAND_MARGIN:
            previous = DELTA_BAND_MED

        self.previous_delta_bucket = previous
        return previous
