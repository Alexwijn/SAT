import logging

from .const import MINIMUM_SETPOINT

_LOGGER = logging.getLogger(__name__)

STABILIZATION_MARGIN = 5
EXCEED_SETPOINT_MARGIN = 2


class BoilerState:
    """
    Represents the operational state of a boiler, including activity, flame status, hot water usage, and current temperature.
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
        self._warming_up = False
        self._adjusting_to_lower_setpoint = False

        self._last_setpoint = None
        self._last_boiler_temperature = None

    def update(self, boiler_temperature: float, boiler_temperature_derivative: float, flame_active: bool, setpoint: float):
        """Update the tracker based on the current boiler temperature, flame status, and setpoint."""
        if setpoint == MINIMUM_SETPOINT:
            return

        if self._last_boiler_temperature is None:
            self._last_boiler_temperature = boiler_temperature

        if self._last_setpoint is None:
            self._last_setpoint = setpoint

        # Detect if setpoint is decreasing
        if setpoint < self._last_setpoint and not self._adjusting_to_lower_setpoint:
            self._adjusting_to_lower_setpoint = True
            _LOGGER.debug("Setpoint decreased. Entering stabilization mode.")

        if not flame_active:
            self._handle_flame_inactive()
        elif self._adjusting_to_lower_setpoint:
            self._handle_adjusting_to_lower_setpoint(boiler_temperature, boiler_temperature_derivative, setpoint)
        elif self._active:
            self._handle_tracking(boiler_temperature, boiler_temperature_derivative, setpoint)

        self._last_setpoint = setpoint
        self._last_boiler_temperature = boiler_temperature

    def _handle_flame_inactive(self):
        """Handle the case where the flame is inactive."""
        if self._active:
            return

        self._active = True
        self._warming_up = True

        _LOGGER.debug("Flame inactive: Starting to track boiler temperature.")

    def _handle_tracking(self, boiler_temperature: float, boiler_temperature_derivative: float, setpoint: float):
        """Handle boiler temperature tracking logic."""
        if not self._warming_up and boiler_temperature_derivative != 0:
            return self._stop_warming_up("Temperature not changing.", boiler_temperature, setpoint)

        if setpoint <= boiler_temperature - EXCEED_SETPOINT_MARGIN:
            return self._stop_tracking("Exceeds setpoint significantly.", boiler_temperature, setpoint)

        if setpoint > boiler_temperature and setpoint - STABILIZATION_MARGIN < boiler_temperature < self._last_boiler_temperature:
            return self._stop_tracking("Stabilizing below setpoint.", boiler_temperature, setpoint)

    def _handle_adjusting_to_lower_setpoint(self, boiler_temperature: float, boiler_temperature_derivative: float, setpoint: float):
        """Handle stabilization when adjusting to a lower setpoint."""
        if boiler_temperature <= setpoint and boiler_temperature_derivative == 0:
            return self._stop_adjusting_to_lower_setpoint("Setpoint stabilization complete.", boiler_temperature, setpoint)

    def _stop_adjusting_to_lower_setpoint(self, reason: str, boiler_temperature: float, setpoint: float):
        """Stop the adjustment to a lower setpoint and log the reason."""
        self._adjusting_to_lower_setpoint = False

        _LOGGER.debug(
            f"Adjustment to lower setpoint stopped: {reason} "
            f"(Setpoint: {setpoint}, Current: {boiler_temperature})."
        )

    def _stop_warming_up(self, reason: str, boiler_temperature: float, setpoint: float):
        """Stop the warming-up phase and log the reason."""
        self._warming_up = False

        _LOGGER.debug(
            f"Warming up stopped: {reason} "
            f"(Setpoint: {setpoint}, Current: {boiler_temperature}, Last: {self._last_boiler_temperature})."
        )

    def _stop_tracking(self, reason: str, boiler_temperature: float, setpoint: float):
        """Deactivate tracking and log the reason."""
        self._active = False

        _LOGGER.debug(
            f"Tracking stopped: {reason} "
            f"(Setpoint: {setpoint}, Current: {boiler_temperature}, Last: {self._last_boiler_temperature})."
        )

    @property
    def active(self) -> bool:
        """Check if the tracker is currently active."""
        return self._active

    @property
    def inactive(self) -> bool:
        """Check if the tracker is currently inactive."""
        return not self._active
