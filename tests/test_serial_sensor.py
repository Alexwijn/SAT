"""Tests for the serial coordinator sensor entities."""

import pytest
from homeassistant.components.sensor import SensorStateClass
from pyotgw.vars import DATA_CH_WATER_PRESS, DATA_CH_WATER_TEMP, DATA_REL_MOD_LEVEL, DATA_SLAVE_OEM_FAULT, DATA_TOTAL_BURNER_STARTS, OTGW_ABOUT

from custom_components.sat.coordinator.serial.sensor import MEASUREMENT_SENSORS, SENSOR_INFO, SatSensor


def _sensor(key: str) -> SatSensor:
    entity = SatSensor.__new__(SatSensor)
    entity._key = key
    return entity


@pytest.mark.parametrize("key", [DATA_CH_WATER_TEMP, DATA_CH_WATER_PRESS, DATA_REL_MOD_LEVEL])
def test_measurement_sensors_have_state_class(key):
    assert _sensor(key).state_class == SensorStateClass.MEASUREMENT


@pytest.mark.parametrize("key", [OTGW_ABOUT, DATA_SLAVE_OEM_FAULT, DATA_TOTAL_BURNER_STARTS])
def test_other_sensors_have_no_state_class(key):
    assert _sensor(key).state_class is None


def test_measurement_sensors_have_a_unit():
    for key in MEASUREMENT_SENSORS:
        assert SENSOR_INFO[key].unit is not None
