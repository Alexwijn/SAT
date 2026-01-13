from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class DeltaBandThresholds:
    low: float
    med: float
    high: float


@dataclass(frozen=True, slots=True)
class RegimeSample:
    setpoint: float
    delta_value: Optional[float]
    outside_temperature: Optional[float]
