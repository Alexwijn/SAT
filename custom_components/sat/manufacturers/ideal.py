from ..manufacturer import Manufacturer, ModulationSuppressionConfig, FlameOffSetpointConfig

FLAME_OFF_SETPOINT_OFFSET_CELSIUS = 18.0
MODULATION_SUPPRESSION_DELAY_SECONDS = 20
MODULATION_SUPPRESSION_OFFSET_CELSIUS = 1.0


class Ideal(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Ideal'

    @property
    def modulation_suppression(self) -> ModulationSuppressionConfig:
        return ModulationSuppressionConfig(
            delay_seconds=MODULATION_SUPPRESSION_DELAY_SECONDS,
            offset_celsius=MODULATION_SUPPRESSION_OFFSET_CELSIUS,
        )

    @property
    def flame_off_setpoint(self) -> FlameOffSetpointConfig:
        return FlameOffSetpointConfig(offset_celsius=FLAME_OFF_SETPOINT_OFFSET_CELSIUS)
