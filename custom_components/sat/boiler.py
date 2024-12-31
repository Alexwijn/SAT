import logging

_LOGGER = logging.getLogger(__name__)

STABILIZATION_MARGIN = 5
EXCEED_SETPOINT_MARGIN = 2


class BoilerState:
    """
    Represents the operational state of a boiler, including activity, flame status,
    hot water usage, and current temperature.
    """

    def __init__(self, device_active: bool, flame_active: bool, hot_water_active: bool, temperature: float):
        """
        Initialize with the boiler's state parameters.

        :param device_active: Whether the boiler is currently operational.
        :param flame_active: Whether the boiler's flame is ignited.
        :param hot_water_active: Whether the boiler is heating water.
        :param temperature: The current boiler temperature in Celsius.
        """
        self._temperature = temperature
        self._flame_active = flame_active
        self._device_active = device_active
        self._hot_water_active = hot_water_active

    @property
    def device_active(self) -> bool:
        """Indicates whether the boiler is running."""
        return self._device_active

    @property
    def flame_active(self) -> bool:
        """Indicates whether the flame is ignited."""
        return self._flame_active

    @property
    def hot_water_active(self) -> bool:
        """Indicates whether the boiler is heating water."""
        return self._hot_water_active

    @property
    def temperature(self) -> float:
        """The boiler's current temperature."""
        return self._temperature


class BoilerTemperatureTracker:
    def __init__(self):
        """Initialize the BoilerTemperatureTracker."""
        self._active = False
        self._last_boiler_temperature = None

    def update(self, boiler_temperature: float, flame_active: bool, setpoint: float):
        """Update the tracker based on the current boiler temperature, flame status, and setpoint."""
        if self._last_boiler_temperature is None:
            self._last_boiler_temperature = boiler_temperature
            _LOGGER.debug("Initialized last_boiler_temperature to %s.", boiler_temperature)

        if not flame_active:
            self._handle_flame_inactive()
        elif self._active:
            self._handle_tracking(boiler_temperature, setpoint)

        self._last_boiler_temperature = boiler_temperature
        _LOGGER.debug("Updated last_boiler_temperature to %s.", boiler_temperature)

    def _handle_flame_inactive(self):
        """Handle the case where the flame is inactive."""
        if self.active:
            return

        self._active = True
        _LOGGER.debug("Flame inactive: Starting to track boiler temperature.")

    def _handle_tracking(self, boiler_temperature: float, setpoint: float):
        """Handle boiler temperature tracking logic."""
        # Stop tracking if the boiler temperature stabilizes below the setpoint
        if setpoint > boiler_temperature and setpoint - STABILIZATION_MARGIN < boiler_temperature < self._last_boiler_temperature:
            return self._stop_tracking("Stabilizing below setpoint.", boiler_temperature, setpoint)

        # Stop tracking if the boiler temperature exceeds the setpoint significantly
        if setpoint <= boiler_temperature - EXCEED_SETPOINT_MARGIN:
            return self._stop_tracking("Exceeds setpoint significantly.", boiler_temperature, setpoint)

    def _stop_tracking(self, reason: str, boiler_temperature: float, setpoint: float):
        """Stop tracking and log the reason."""
        self._active = False

        _LOGGER.debug(
            f"Stopped tracking boiler temperature: {reason} "
            f"Setpoint: {setpoint}, Current: {boiler_temperature}, "
            f"Last: {self._last_boiler_temperature}."
        )

    @property
    def active(self) -> bool:
        """Check if the tracker is currently active."""
        return self._active

    @property
    def inactive(self) -> bool:
        """Check if the tracker is currently inactive."""
        return not self._active
