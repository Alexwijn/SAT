"""Fixtures for testing."""
import pytest
from _pytest.logging import LogCaptureFixture
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import assert_setup_component, MockConfigEntry

from custom_components.sat import DOMAIN, CLIMATE, COORDINATOR
from custom_components.sat.climate import SatClimate
from custom_components.sat.fake import SatFakeCoordinator
from tests.const import DEFAULT_USER_DATA


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def entry(hass: HomeAssistant, domains: list, test_data: dict, config: dict, caplog: LogCaptureFixture) -> MockConfigEntry:
    """Setup any given integration."""
    for domain, count in domains:
        with assert_setup_component(count, domain):
            assert await async_setup_component(hass, domain, config)

        await hass.async_block_till_done()

    await hass.async_start()
    await hass.async_block_till_done()

    user_data = DEFAULT_USER_DATA.copy()
    user_data.update(test_data)

    config_entry = MockConfigEntry(domain=DOMAIN, data=user_data)
    await hass.config_entries.async_add(config_entry)

    return config_entry


@pytest.fixture
async def climate(hass, entry: MockConfigEntry) -> SatClimate:
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id][CLIMATE]


@pytest.fixture
async def coordinator(hass, entry: MockConfigEntry) -> SatFakeCoordinator:
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id][COORDINATOR]
