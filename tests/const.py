from custom_components.sat.const import (
    CONF_AUTOMATIC_GAINS,
    CONF_HEATING_SYSTEM,
    CONF_INSIDE_SENSOR_ENTITY_ID,
    CONF_MODE,
    CONF_NAME,
    CONF_OUTSIDE_SENSOR_ENTITY_ID,
    CONF_OVERSHOOT_PROTECTION,
    HeatingSystem,
    OPTIONS_DEFAULTS,
)
from custom_components.sat.entry_data import SatConfig, SatMode

DEFAULT_USER_DATA = {
    CONF_NAME: "Test",
    CONF_MODE: SatMode.FAKE,
    CONF_AUTOMATIC_GAINS: True,
    CONF_OVERSHOOT_PROTECTION: True,
    CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
    CONF_INSIDE_SENSOR_ENTITY_ID: "sensor.test_inside_sensor",
    CONF_OUTSIDE_SENSOR_ENTITY_ID: "sensor.test_outside_sensor",
}


def make_config(data=None, options=None, entry_id: str = "test") -> SatConfig:
    config_data = {**DEFAULT_USER_DATA}
    config_data.update(data or {})

    return SatConfig(entry_id=entry_id, data=config_data, options={**OPTIONS_DEFAULTS, **(options or {})})
