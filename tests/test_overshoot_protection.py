"""Exercise overshoot calculation with native coordinator data updates."""

import asyncio
from contextlib import suppress

import pytest
from homeassistant.core import HomeAssistant

from custom_components.sat import overshoot_protection
from custom_components.sat.const import (
    CONF_HEATING_SYSTEM,
    CONF_MINIMUM_SETPOINT,
    CONF_MQTT_TOPIC,
    CONF_SIMULATION,
    HEATING_SYSTEM_RADIATORS,
    MODE_MQTT_OPENTHERM,
)
from custom_components.sat.coordinator import SatDataUpdateCoordinatorFactory
from custom_components.sat.mqtt.opentherm import (
    DATA_BOILER_TEMPERATURE,
    DATA_CENTRAL_HEATING,
    DATA_CONTROL_SETPOINT,
    DATA_FLAME_ACTIVE,
    DATA_MAXIMUM_CONTROL_SETPOINT,
    DATA_REL_MOD_LEVEL,
    STATE_ON,
    SatOpenThermMqttCoordinator,
)
from custom_components.sat.overshoot_protection import OvershootProtection


@pytest.fixture
def coordinator(hass: HomeAssistant) -> SatOpenThermMqttCoordinator:
    """Use the production coordinator without publishing device commands."""
    result = SatDataUpdateCoordinatorFactory.resolve(
        hass,
        MODE_MQTT_OPENTHERM,
        "test-gateway",
        {
            CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
            CONF_MINIMUM_SETPOINT: 57,
            CONF_MQTT_TOPIC: "test",
        },
        {CONF_SIMULATION: True},
    )
    result.async_set_updated_data({
        DATA_CENTRAL_HEATING: STATE_ON,
        DATA_FLAME_ACTIVE: STATE_ON,
        DATA_CONTROL_SETPOINT: "57",
        DATA_MAXIMUM_CONTROL_SETPOINT: "75",
        DATA_REL_MOD_LEVEL: "0",
    })
    return result


@pytest.fixture
def protection(
    coordinator: SatOpenThermMqttCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> OvershootProtection:
    """Shorten the polling interval without replacing asyncio scheduling."""
    monkeypatch.setattr(overshoot_protection, "SLEEP_INTERVAL", 0.001)
    return OvershootProtection(coordinator, HEATING_SYSTEM_RADIATORS)


async def _publish_temperatures(coordinator: SatOpenThermMqttCoordinator) -> None:
    await asyncio.sleep(0.01)
    coordinator.async_set_updated_data({DATA_BOILER_TEMPERATURE: "40.0"})
    await asyncio.sleep(0.01)
    coordinator.async_set_updated_data({DATA_BOILER_TEMPERATURE: "40.2"})


@pytest.mark.parametrize("initial_temperature", [None, "0"], ids=["unavailable", "zero"])
async def test_waits_for_delayed_temperature(
    coordinator: SatOpenThermMqttCoordinator,
    protection: OvershootProtection,
    initial_temperature: str | None,
) -> None:
    if initial_temperature is not None:
        coordinator.async_set_updated_data({DATA_BOILER_TEMPERATURE: initial_temperature})
    updater = asyncio.create_task(_publish_temperatures(coordinator))
    try:
        result = await asyncio.wait_for(protection.calculate(), timeout=1)
        await updater
        assert result == 40.2
        assert coordinator.boiler_temperature == 40.2
    finally:
        updater.cancel()
        with suppress(asyncio.CancelledError):
            await updater


@pytest.mark.parametrize("initial_temperature", [None, "0"], ids=["unavailable", "zero"])
async def test_missing_temperature_obeys_stable_wait_timeout(
    coordinator: SatOpenThermMqttCoordinator,
    protection: OvershootProtection,
    monkeypatch: pytest.MonkeyPatch,
    initial_temperature: str | None,
) -> None:
    if initial_temperature is not None:
        coordinator.async_set_updated_data({DATA_BOILER_TEMPERATURE: initial_temperature})
    monkeypatch.setattr(
        overshoot_protection, "OVERSHOOT_PROTECTION_STABLE_WAIT", 0.02
    )
    with pytest.raises(TimeoutError):
        await protection.calculate()


async def test_missing_temperature_can_be_cancelled(
    protection: OvershootProtection,
) -> None:
    task = asyncio.create_task(protection.calculate())
    try:
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_available_temperature_still_completes(
    coordinator: SatOpenThermMqttCoordinator,
    protection: OvershootProtection,
) -> None:
    coordinator.async_set_updated_data({DATA_BOILER_TEMPERATURE: "40.0"})
    updater = asyncio.create_task(_publish_temperatures(coordinator))
    try:
        result = await asyncio.wait_for(protection.calculate(), timeout=1)
        await updater
        assert result == 40.2
    finally:
        updater.cancel()
        with suppress(asyncio.CancelledError):
            await updater


@pytest.mark.parametrize("failure", ["timeout", "cancelled"])
async def test_failed_calculation_resets_heater(
    coordinator, protection, monkeypatch, failure,
):
    from unittest.mock import AsyncMock
    from custom_components.sat.coordinator import DeviceState
    from custom_components.sat.const import MINIMUM_SETPOINT

    heater = AsyncMock(wraps=coordinator.async_set_heater_state)
    setpoint = AsyncMock(wraps=coordinator.async_set_control_setpoint)
    monkeypatch.setattr(coordinator, "async_set_heater_state", heater)
    monkeypatch.setattr(coordinator, "async_set_control_setpoint", setpoint)
    monkeypatch.setattr(overshoot_protection, "OVERSHOOT_PROTECTION_STABLE_WAIT", 0.02)
    if failure == "timeout":
        with pytest.raises(TimeoutError):
            await protection.calculate()
    else:
        task = asyncio.create_task(protection.calculate())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    heater.assert_awaited_once_with(DeviceState.OFF)
    setpoint.assert_awaited_once_with(MINIMUM_SETPOINT)
