from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceCapabilities:
    # Setpoint limits
    maximum_setpoint: float
    minimum_setpoint: float


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceState:
    # Activity state
    flame_active: bool
    hot_water_active: bool
    central_heating: bool

    # Temperatures / modulation
    setpoint: Optional[float]
    flow_temperature: Optional[float]
    return_temperature: Optional[float]
    max_modulation_level: Optional[float]
    relative_modulation_level: Optional[float]