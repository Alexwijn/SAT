from __future__ import annotations

IN_BAND_MARGIN_CELSIUS: float = 1.0
MAX_ON_DURATION_SECONDS_FOR_ROLLING_WINDOWS: float = 1800.0

# Minimum cycle duration required for classification.
TARGET_MIN_ON_TIME_SECONDS: float = 600.0

# Flow vs. setpoint classification margins
OVERSHOOT_MARGIN_CELSIUS: float = 3.0
UNDERSHOOT_MARGIN_CELSIUS: float = -3.0

# Some boilers flag hot water a few seconds after (or before) the flame shows up
HOT_WATER_LEAD_SECONDS: float = 30.0

# A flame that relights this soon after dropping out within the ignition window is the same burn
IGNITION_WINDOW_SECONDS: float = 30.0
IGNITION_RELIGHT_SECONDS: float = 10.0

# Timeouts
LAST_CYCLE_MAX_AGE_SECONDS: float = 6 * 3600

# Cycle history windows
DEFAULT_DUTY_WINDOW_SECONDS: int = 15 * 60
DEFAULT_CYCLES_WINDOW_SECONDS: int = 60 * 60
DEFAULT_MEDIAN_WINDOW_SECONDS: int = 4 * 60 * 60
DAILY_WINDOW_SECONDS: int = 24 * 60 * 60
