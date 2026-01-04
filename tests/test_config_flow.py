from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.entry_data import SatMode


async def test_create_coordinator(hass):
    flow_handler = SatFlowHandler()
    flow_handler.data = {
        "name": "Test",
        "mode": SatMode.FAKE,
        "device": "test_device",
    }

    await flow_handler.async_create_coordinator()
