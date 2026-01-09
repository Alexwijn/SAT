from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeltaBandThresholds:
    low: float
    med: float
    high: float
