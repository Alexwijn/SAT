import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.helpers import issue_registry as ir

from custom_components.sat.const import CONF_RADIATORS, CONF_THERMOSTAT, DOMAIN, INSIDE_FALLBACK_DELAY_SECONDS
from custom_components.sat.helpers import timestamp
from custom_components.sat.inside_temperature import InsideTemperature
from custom_components.sat.types import InsideSource
from tests.const import make_config

INSIDE = "sensor.test_inside_sensor"
THERMOSTAT = "climate.wall"
RADIATORS = ["climate.radiator_left", "climate.radiator_right"]


@pytest.fixture
def inside_temperature():
    return InsideTemperature(make_config(data={CONF_THERMOSTAT: THERMOSTAT, CONF_RADIATORS: RADIATORS}))


def _lose_sensor(hass, inside_temperature):
    hass.states.async_set(INSIDE, "unavailable")
    inside_temperature.observe(hass, 1000.0)
    inside_temperature.observe(hass, 1000.0 + INSIDE_FALLBACK_DELAY_SECONDS)


async def test_short_outage_does_not_switch_the_source(hass, inside_temperature):
    hass.states.async_set(INSIDE, "unavailable")
    hass.states.async_set(THERMOSTAT, "heat", {"current_temperature": 19.5})

    inside_temperature.observe(hass, 1000.0)

    assert inside_temperature.read(hass) is None


async def test_thermostat_takes_over_a_lost_sensor(hass, inside_temperature):
    hass.states.async_set(THERMOSTAT, "heat", {"current_temperature": 19.5})
    _lose_sensor(hass, inside_temperature)

    reading = inside_temperature.read(hass)

    assert reading.temperature == 19.5
    assert reading.source is InsideSource.THERMOSTAT


async def test_radiators_take_over_without_a_thermostat(hass, inside_temperature):
    for entity_id, temperature in zip(RADIATORS, (20.0, 22.0)):
        hass.states.async_set(entity_id, "heat", {"current_temperature": temperature})

    _lose_sensor(hass, inside_temperature)

    reading = inside_temperature.read(hass)

    assert reading.temperature == 21.0
    assert reading.source is InsideSource.RADIATORS


async def test_sensor_takes_back_over_at_once(hass, inside_temperature):
    hass.states.async_set(THERMOSTAT, "heat", {"current_temperature": 19.5})
    _lose_sensor(hass, inside_temperature)

    hass.states.async_set(INSIDE, "20")

    assert inside_temperature.read(hass).source is InsideSource.SENSOR


@pytest.mark.parametrize(("domains", "data", "options", "config"), [([], {CONF_RADIATORS: RADIATORS}, {}, {})])
async def test_fallback_restarts_the_pid_and_raises_an_issue(hass, entry, demand_control, monkeypatch, domains, data, options, config):
    issue_id = f"running_on_fallback_sensor_{entry.entry_id}"
    resets = []
    monkeypatch.setattr(demand_control.pid, "reset", lambda: resets.append(True))
    demand_control._hvac_mode = HVACMode.HEAT
    now = timestamp()

    hass.states.async_set(INSIDE, "unavailable")
    hass.states.async_set(RADIATORS[0], "heat", {"current_temperature": 21.5})

    demand_control._follow_inside_source(now)
    demand_control._follow_inside_source(now + INSIDE_FALLBACK_DELAY_SECONDS)

    assert demand_control.current_temperature == 21.5
    assert resets == [True]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    hass.states.async_set(INSIDE, "20")
    demand_control._follow_inside_source(now + INSIDE_FALLBACK_DELAY_SECONDS + 5.0)

    assert resets == [True, True]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.parametrize(("domains", "data", "options", "config"), [([], {CONF_RADIATORS: RADIATORS}, {}, {})])
async def test_nothing_is_learned_from_a_fallback_source(hass, demand_control, monkeypatch, domains, data, options, config):
    recorded = []
    monkeypatch.setattr(demand_control, "_record_room_observations", lambda *args: recorded.append(args))
    demand_control._hvac_mode = HVACMode.HEAT
    demand_control._target_temperature = 21.0
    now = timestamp()

    hass.states.async_set(INSIDE, "unavailable")
    hass.states.async_set(RADIATORS[0], "heat", {"current_temperature": 21.5})
    demand_control._follow_inside_source(now)
    demand_control._follow_inside_source(now + INSIDE_FALLBACK_DELAY_SECONDS)

    demand_control.control_pid()

    assert recorded == []
