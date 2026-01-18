from __future__ import annotations

IN_BAND_MARGIN_CELSIUS: float = 1.0
MAX_ON_DURATION_SECONDS_FOR_ROLLING_WINDOWS: float = 1800.0

# Below this, if we overshoot / underheat, we call it "too short".
TARGET_MIN_ON_TIME_SECONDS: float = 600.0
ULTRA_SHORT_MIN_ON_TIME_SECONDS: float = 90.0

# Flow vs. setpoint classification margins
OVERSHOOT_MARGIN_CELSIUS: float = 3.0
UNDERSHOOT_MARGIN_CELSIUS: float = -3.0
OVERSHOOT_SUSTAIN_SECONDS: float = 60.0

# Timeouts
LAST_CYCLE_MAX_AGE_SECONDS: float = 6 * 3600

# Cycle history windows
DEFAULT_DUTY_WINDOW_SECONDS: int = 15 * 60
DEFAULT_CYCLES_WINDOW_SECONDS: int = 60 * 60
DEFAULT_MEDIAN_WINDOW_SECONDS: int = 4 * 60 * 60
