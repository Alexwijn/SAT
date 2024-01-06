from custom_components.sat import MODE_FAKE
from custom_components.sat.config_flow import SatFlowHandler


async def test_create_coordinator(hass):
    flow_handler = SatFlowHandler()
    flow_handler.data = {
        "name": "Test",
        "mode": MODE_FAKE,
        "device": "test_device",
    }

    await flow_handler.async_create_coordinator()
