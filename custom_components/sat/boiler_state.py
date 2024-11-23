class BoilerState:
    """
    Represents the operational state of a boiler, including activity, flame status,
    hot water usage, and current temperature.
    """
    def __init__(
        self,
        device_active: bool,
        flame_active: bool,
        hot_water_active: bool,
        temperature: float
    ):
        """
        Initialize with the boiler's state parameters.

        :param device_active: Whether the boiler is currently operational.
        :param flame_active: Whether the boiler's flame is ignited.
        :param hot_water_active: Whether the boiler is heating water.
        :param temperature: The current boiler temperature in degrees Celsius.
        """
        self._device_active = device_active
        self._flame_active = flame_active
        self._hot_water_active = hot_water_active
        self._temperature = temperature

    @property
    def device_active(self) -> bool:
        """
        Indicates whether the boiler is running.
        """
        return self._device_active

    @property
    def flame_active(self) -> bool:
        """
        Indicates whether the flame is ignited.
        """
        return self._flame_active

    @property
    def hot_water_active(self) -> bool:
        """
        Indicates whether the boiler is heating water.
        """
        return self._hot_water_active

    @property
    def temperature(self) -> float:
        """
        The boiler's current temperature.
        """
        return self._temperature
