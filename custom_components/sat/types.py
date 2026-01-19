from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Optional


@dataclass(frozen=True, slots=True)
class Percentiles:
    """Summary percentiles for a sampled metric."""
    p50: Optional[float] = None
    p90: Optional[float] = None


class BoilerStatus(Enum):
    """Device operating status, grouped by idle, preheat, modulation, and cooling phases."""

    # Idle/unknown
    OFF = "off"
    IDLE = "idle"
    INSUFFICIENT_DATA = "insufficient_data"

    # Preheat / ignition
    PREHEATING = "preheating"
    AT_SETPOINT_BAND = "at_setpoint_band"
    STALLED_IGNITION = "stalled_ignition"

    # Modulation / active heating
    MODULATING_UP = "modulating_up"
    MODULATING_DOWN = "modulating_down"
    IGNITION_SURGE = "ignition_surge"
    CENTRAL_HEATING = "central_heating"
    HEATING_HOT_WATER = "heating_hot_water"

    # Cooling / safety / cycling
    COOLING = "cooling"
    ANTI_CYCLING = "anti_cycling"
    PUMP_STARTING = "pump_starting"
    WAITING_FOR_FLAME = "waiting_for_flame"
    OVERSHOOT_COOLING = "overshoot_cooling"
    POST_CYCLE_SETTLING = "post_cycle_settling"


class CycleKind(str, Enum):
    """Cycle category for tracking heating vs hot water activity."""
    MIXED = "mixed"
    UNKNOWN = "unknown"
    CENTRAL_HEATING = "central_heating"
    DOMESTIC_HOT_WATER = "domestic_hot_water"


class CycleClassification(str, Enum):
    """Cycle quality classification, including duration and overshoot/underheat behavior."""
    # Good/unknown
    GOOD = "good"
    UNCERTAIN = "uncertain"
    INSUFFICIENT_DATA = "insufficient_data"

    # Overshoot/underheat outcomes
    OVERSHOOT = "overshoot"
    UNDERHEAT = "underheat"

    # PWM short cycling
    SHORT_CYCLING = "short_cycling"


class HeaterState(str, Enum):
    """Binary device on/off state."""
    ON = "on"
    OFF = "off"


class PWMStatus(str, Enum):
    """Pulse-width modulation controller state."""
    ON = "on"
    OFF = "off"
    IDLE = "idle"


class CycleControlMode(str, Enum):
    """Control mode used during a cycle."""
    PWM = "pwm"
    CONTINUOUS = "continuous"


class RelativeModulationState(str, Enum):
    """Relative modulation mode/state used for control decisions."""
    OFF = "off"
    COLD = "cold"
    PWM_OFF = "pwm_off"
    HOT_WATER = "hot_water"


class HeatingSystem(StrEnum):
    UNKNOWN = "unknown"
    HEAT_PUMP = "heat_pump"
    RADIATORS = "radiators"
    UNDERFLOOR = "underfloor"

    @property
    def base_offset(self) -> float:
        return 20 if self == HeatingSystem.UNDERFLOOR else 27.2


class HeatingMode(StrEnum):
    ECO = "eco"
    COMFORT = "comfort"
