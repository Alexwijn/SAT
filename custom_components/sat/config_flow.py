"""Adds config flow for SAT."""
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import dhcp
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from pyotgw import OpenThermGateway

from .const import (
    CONF_ID,
    CONF_NAME,
    CONF_DEVICE,
    DOMAIN,
    CONF_INSIDE_SENSOR_ENTITY_ID,
    CONF_OUTSIDE_SENSOR_ENTITY_ID, CONF_HEATING_CURVE, CONF_HEATING_SYSTEM, CONF_HEATING_CURVE_MOVE, CONF_UNDERFLOOR,
    CONF_RADIATOR_LOW_TEMPERATURES, CONF_RADIATOR_HIGH_TEMPERATURES, OPTIONS_DEFAULTS, CONF_SIMULATION,
)

_LOGGER = logging.getLogger(__name__)


class SatFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for SAT."""
    VERSION = 1

    def __init__(self):
        """Initialize."""
        self._data = {}
        self._errors = {}

    async def async_step_dhcp(self, discovery_info: dhcp.DhcpServiceInfo) -> FlowResult:
        """Handle dhcp discovery."""
        self._data.update({CONF_DEVICE: discovery_info.ip})
        _LOGGER.debug("Discovered OTGW at [%s]", discovery_info.ip)

        return await self.async_step_user()

    async def async_step_user(self, user_input=None) -> FlowResult:
        self._errors = {}

        if user_input is not None:
            self._data.update(user_input)
            valid = await self._test_gateway_connection(user_input[CONF_DEVICE])

            if not valid:
                self._errors["base"] = "auth"
                return await self.async_step_gateway_setup()

            return await self.async_step_sensors_setup()

        return await self.async_step_gateway_setup()

    async def async_step_gateway_setup(self):
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ID, default="home"): str,
                vol.Required(CONF_NAME, default="Home"): str,
                vol.Required(CONF_DEVICE, default="socket://192.168.178.21:25238"): str,
            }),
        )

    async def async_step_sensors(self, user_input=None):
        self._errors = {}

        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)

        return await self.async_step_sensors_setup()

    async def async_step_sensors_setup(self):
        entity_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=SENSOR_DOMAIN)
        )

        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema({
                vol.Required(CONF_INSIDE_SENSOR_ENTITY_ID): entity_selector,
                vol.Optional(CONF_OUTSIDE_SENSOR_ENTITY_ID): entity_selector,
            }),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return SatOptionsFlowHandler(config_entry)

    @staticmethod
    async def _test_gateway_connection(device: str):
        """Return true if credentials is valid."""
        return await OpenThermGateway().connect(port=device, skip_init=True)


class SatOptionsFlowHandler(config_entries.OptionsFlow):
    """Config flow options handler."""

    def __init__(self, config_entry: ConfigEntry):
        """Initialize HACS options flow."""
        self._config_entry = config_entry
        self._options = dict(config_entry.options)

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            self._options.update(user_input)
            return self.async_create_entry(title=self._config_entry.data[CONF_NAME], data=self._options)

        defaults = OPTIONS_DEFAULTS.copy()
        defaults.update(self._options)

        schema = {
            vol.Required(CONF_HEATING_CURVE, default=defaults[CONF_HEATING_CURVE]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.1, max=3.0, step=0.1)
            ),
            vol.Required(CONF_HEATING_CURVE_MOVE, default=defaults[CONF_HEATING_CURVE_MOVE]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-15, max=15, step=0.5)
            ),
            vol.Required(CONF_HEATING_SYSTEM, default=defaults[CONF_HEATING_SYSTEM]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[
                    {"value": CONF_RADIATOR_HIGH_TEMPERATURES, "label": "Radiators ( High Temperatures )"},
                    {"value": CONF_RADIATOR_LOW_TEMPERATURES, "label": "Radiators ( Low Temperatures )"},
                    {"value": CONF_UNDERFLOOR, "label": "Underfloor"}
                ])
            ),
        }

        if self.show_advanced_options:
            schema.update({
                vol.Required(CONF_SIMULATION, default=defaults[CONF_SIMULATION]): bool
            })

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema)
        )
