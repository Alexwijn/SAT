from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .const import (
    DELTA_BAND_HIGH,
    DELTA_BAND_LOW,
    DELTA_BAND_MED,
    DELTA_BAND_UNKNOWN,
    DELTA_BAND_VLOW,
    MIN_STABLE_CYCLES_TO_TRUST,
    OUTSIDE_BAND_COLD,
    OUTSIDE_BAND_FREEZING,
    OUTSIDE_BAND_MILD,
    OUTSIDE_BAND_UNKNOWN,
    OUTSIDE_BAND_WARM,
)
from .regimes import RegimeKey, RegimeState


@dataclass(frozen=True, slots=True)
class RegimeDistance:
    setpoint: int
    outside: int
    delta: int

    @property
    def total(self) -> int:
        return self.setpoint + self.outside + self.delta


@dataclass(frozen=True, slots=True)
class WeightedRegime:
    key: RegimeKey
    state: RegimeState
    distance: RegimeDistance


class RegimeSeeder:
    @staticmethod
    def initial_minimum(active_key: RegimeKey, regimes: dict[RegimeKey, RegimeState], current_value: Optional[float]) -> Optional[float]:
        if not regimes:
            return None

        temperature_band_order: dict[str, int] = {
            OUTSIDE_BAND_UNKNOWN: 0,
            OUTSIDE_BAND_FREEZING: 1,
            OUTSIDE_BAND_COLD: 2,
            OUTSIDE_BAND_MILD: 3,
            OUTSIDE_BAND_WARM: 4,
        }

        delta_band_order: dict[str, int] = {
            DELTA_BAND_UNKNOWN: 0,
            DELTA_BAND_VLOW: 1,
            DELTA_BAND_LOW: 2,
            DELTA_BAND_MED: 3,
            DELTA_BAND_HIGH: 4,
        }

        trusted_regimes = {
            key: state
            for key, state in regimes.items()
            if (state.stable_cycles >= MIN_STABLE_CYCLES_TO_TRUST) and (state.completed_cycles >= 3)
        }

        if not trusted_regimes:
            return None

        parsed_regimes = list(trusted_regimes.items())
        if not parsed_regimes:
            return None

        def regime_distance(parsed: RegimeKey) -> RegimeDistance:
            temperature_a = temperature_band_order.get(parsed.outside_band, 0)
            temperature_b = temperature_band_order.get(active_key.outside_band, 0)
            delta_a = delta_band_order.get(parsed.delta_band, 0)
            delta_b = delta_band_order.get(active_key.delta_band, 0)
            return RegimeDistance(
                setpoint=abs(parsed.setpoint_band - active_key.setpoint_band),
                outside=abs(temperature_a - temperature_b),
                delta=abs(delta_a - delta_b),
            )

        def distance_weight(distance: int) -> float:
            return 1.0 / (1.0 + float(distance))

        weighted_regimes = [
            WeightedRegime(key=key, state=state, distance=regime_distance(key))
            for key, state in parsed_regimes
        ]

        weighted_regimes.sort(key=lambda item: item.distance.total)
        closest_regimes = weighted_regimes[:3]

        weighted_total = 0.0
        weight_sum = 0.0
        for item in closest_regimes:
            weight = distance_weight(item.distance.total)
            weighted_total += item.state.minimum_setpoint * weight
            weight_sum += weight

        if weight_sum <= 0.0:
            return None

        blended = weighted_total / weight_sum
        if current_value is not None:
            return round(0.7 * current_value + 0.3 * blended, 1)

        return round(blended, 1)
