"""Solar gain compensation controller."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .const import SOLAR_GAIN_HOLD_SECONDS, SOLAR_GAIN_MAX_RELATIVE_MODULATION_PERCENT
from .entry_data import SolarGainConfig


@dataclass(frozen=True)
class SolarGainSample:
    """Single indoor temperature sample used to derive rise rate."""

    temperature: float
    timestamp: float


@dataclass(frozen=True)
class SolarGainSignals:
    """Input signals required to evaluate solar gain state."""

    sample: SolarGainSample
    valves_open: bool
    sun_elevation: Optional[float]
    flame_active: bool
    is_heating_mode: bool
    relative_modulation: Optional[float]


@dataclass(frozen=True)
class SolarGainSnapshot:
    """Current solar gain evaluation output."""

    active: bool
    rise_per_hour: Optional[float]
    sun_elevation: Optional[float]


class SolarGainController:
    """Detect passive solar gain and keep a short-lived suppression hold."""

    def __init__(self, config: SolarGainConfig) -> None:
        self._config = config
        self._hold_until: Optional[float] = None
        self._last_sample: Optional[SolarGainSample] = None

    def update(self, signals: SolarGainSignals) -> SolarGainSnapshot:
        rise_per_hour = self._indoor_rise_per_hour(signals.sample)
        sun_elevation = signals.sun_elevation

        if not self._config.enabled or not signals.is_heating_mode:
            self._hold_until = None
            return SolarGainSnapshot(active=False, rise_per_hour=rise_per_hour, sun_elevation=sun_elevation)

        if sun_elevation is None:
            self._hold_until = None
            return SolarGainSnapshot(active=False, rise_per_hour=rise_per_hour, sun_elevation=None)

        if self._is_solar_gain_event(signals, rise_per_hour):
            self._hold_until = signals.sample.timestamp + SOLAR_GAIN_HOLD_SECONDS

        active = self._hold_until is not None and signals.sample.timestamp <= self._hold_until
        return SolarGainSnapshot(active=active, rise_per_hour=rise_per_hour, sun_elevation=sun_elevation)

    def _indoor_rise_per_hour(self, sample: SolarGainSample) -> Optional[float]:
        if self._last_sample is None:
            self._last_sample = sample
            return None

        previous = self._last_sample
        self._last_sample = sample

        elapsed_seconds = sample.timestamp - previous.timestamp
        if elapsed_seconds <= 0:
            return None

        return (sample.temperature - previous.temperature) * 3600 / elapsed_seconds

    def _is_solar_gain_event(self, signals: SolarGainSignals, rise_per_hour: Optional[float]) -> bool:
        if rise_per_hour is None or signals.sun_elevation is None:
            return False

        if signals.sun_elevation < self._config.minimum_elevation:
            return False

        if rise_per_hour < self._config.minimum_rise_per_hour:
            return False

        if not signals.valves_open:
            return False

        return self._boiler_output_is_low(signals.flame_active, signals.relative_modulation)

    @staticmethod
    def _boiler_output_is_low(flame_active: bool, relative_modulation: Optional[float]) -> bool:
        if not flame_active:
            return True

        if relative_modulation is None:
            return False

        return relative_modulation <= SOLAR_GAIN_MAX_RELATIVE_MODULATION_PERCENT
