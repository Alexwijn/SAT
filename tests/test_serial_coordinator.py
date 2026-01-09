"""Unit tests for the serial coordinator."""

import pytest

from custom_components.sat.const import CONF_DEVICE, CONF_MODE, OPTIONS_DEFAULTS
from custom_components.sat.entry_data import SatConfig, SatMode
from custom_components.sat.coordinator.serial import SatSerialCoordinator
from tests.const import DEFAULT_USER_DATA


class DummyGateway:
    """Minimal OpenThermGateway stand-in used for testing."""

    def __init__(self, tracker):
        tracker["instance"] = self
        self.subscriptions = []
        self.reset_calls = []
        self.connected = False
        self.disconnected = False

    async def connect(self, port, timeout):
        self.connected = True
        self.port = port
        self.timeout = timeout

    def subscribe(self, callback):
        self.subscriptions.append(callback)

    def unsubscribe(self, callback):
        self.subscriptions.remove(callback)

    async def set_control_setpoint(self, value):
        self.reset_calls.append(("set_control_setpoint", value))

    async def set_max_relative_mod(self, value):
        self.reset_calls.append(("set_max_relative_mod", value))

    async def disconnect(self):
        self.disconnected = True

    async def set_dhw_setpoint(self, value):
        self.reset_calls.append(("set_dhw_setpoint", value))

    async def set_target_temp(self, value):
        self.reset_calls.append(("set_target_temp", value))

    async def set_ch_enable_bit(self, value):
        self.reset_calls.append(("set_ch_enable_bit", value))

    async def set_max_ch_setpoint(self, value):
        self.reset_calls.append(("set_max_ch_setpoint", value))


def _make_serial_config():
    data = {**DEFAULT_USER_DATA, CONF_MODE: SatMode.SERIAL, CONF_DEVICE: "/dev/ttyUSB0"}
    return SatConfig(entry_id="serial-test", data=data, options={**OPTIONS_DEFAULTS})


@pytest.fixture
def serial_gateway_monkeypatch(monkeypatch):
    tracker: dict[str, DummyGateway | None] = {}

    def _factory():
        return DummyGateway(tracker)

    monkeypatch.setattr("custom_components.sat.coordinator.serial.OpenThermGateway", _factory)
    return tracker


@pytest.mark.asyncio
async def test_serial_coordinator_manages_subscription(hass, serial_gateway_monkeypatch):
    coordinator = SatSerialCoordinator(hass, _make_serial_config())
    gateway = serial_gateway_monkeypatch["instance"]

    assert gateway.subscriptions == [coordinator._publish_callback]

    await coordinator.async_setup()
    await hass.async_block_till_done()

    await coordinator.async_will_remove_from_hass()

    assert gateway.subscriptions == []
    assert gateway.disconnected
    assert ("set_control_setpoint", 0) in gateway.reset_calls
    assert ("set_max_relative_mod", "-") in gateway.reset_calls


@pytest.mark.asyncio
async def test_serial_hot_water_setpoint_calls_correct_super(monkeypatch, hass, serial_gateway_monkeypatch):
    keeper: dict[str, list] = {"hot_water": [], "thermostat": []}

    async def hot_water_stub(self, value):
        keeper["hot_water"].append((self, value))

    async def thermostat_stub(self, value):
        keeper["thermostat"].append((self, value))

    monkeypatch.setattr(
        "custom_components.sat.coordinator.SatDataUpdateCoordinator.async_set_control_hot_water_setpoint",
        hot_water_stub,
    )
    monkeypatch.setattr(
        "custom_components.sat.coordinator.SatDataUpdateCoordinator.async_set_control_thermostat_setpoint",
        thermostat_stub,
    )

    coordinator = SatSerialCoordinator(hass, _make_serial_config())
    await coordinator.async_setup()

    await coordinator.async_set_control_hot_water_setpoint(47.5)

    assert keeper["hot_water"] == [(coordinator, 47.5)]
    assert keeper["thermostat"] == []
