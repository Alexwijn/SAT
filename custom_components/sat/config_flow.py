"""Adds config flow for SAT."""
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import dhcp
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from pyotgw import OpenThermGateway

from .const import *

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

    async def async_step_user(self, _user_input=None) -> FlowResult:
        self._errors = {}

        if _user_input is not None:
            self._data.update(_user_input)

            if not await self._test_gateway_connection():
                self._errors["base"] = "auth"
                return await self.async_step_gateway_setup()

            return await self.async_step_sensors_setup()

        return await self.async_step_gateway_setup()

    async def async_step_gateway_setup(self):
        return self.async_show_form(
            step_id="user",
            last_step=False,
            errors=self._errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default="Living Room"): str,
                vol.Required(CONF_DEVICE, default="socket://otgw.local:25238"): str,
            }),
        )

    async def async_step_sensors(self, _user_input=None):
        self._errors = {}

        if _user_input is not None:
            self._data.update(_user_input)
            return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)

        return await self.async_step_sensors_setup()

    async def async_step_sensors_setup(self):
        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema({
                vol.Required(CONF_INSIDE_SENSOR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=[SENSOR_DOMAIN])
                ),
                vol.Required(CONF_OUTSIDE_SENSOR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=[SENSOR_DOMAIN, WEATHER_DOMAIN], multiple=True)
                ),
            }),
        )

    async def _test_gateway_connection(self):
        """Return true if credentials is valid."""
        return await OpenThermGateway().connect(port=self._data[CONF_DEVICE], skip_init=True, timeout=5)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return SatOptionsFlowHandler(config_entry)


class SatOptionsFlowHandler(config_entries.OptionsFlow):
    """Config flow options handler."""

    def __init__(self, config_entry: ConfigEntry):
        self._config_entry = config_entry
        self._options = dict(config_entry.options)

    async def async_step_init(self, _user_input=None):
        return await self.async_step_user(_user_input)

    async def async_step_user(self, _user_input=None) -> FlowResult:
        menu_options = {
            "general": "General",
            "presets": "Presets",
            "climates": "Climates (multi-room)",
        }

        if self.show_advanced_options:
            menu_options.update({
                "advanced": "Advanced"
            })

        return self.async_show_menu(
            step_id="user",
            menu_options=menu_options
        )

    async def async_step_general(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            return await self.update_options(_user_input)

        defaults = await self.get_options()
        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema({
                vol.Required(CONF_HEATING_CURVE, default=defaults[CONF_HEATING_CURVE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=-15, max=15, step=0.1)
                ),
                vol.Required(CONF_HEATING_CURVE_MOVE, default=defaults[CONF_HEATING_CURVE_MOVE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=-15, max=15, step=0.5)
                ),
                vol.Required(CONF_TARGET_TEMPERATURE_STEP, default=defaults[CONF_TARGET_TEMPERATURE_STEP]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.1, max=1, step=0.05)
                ),
                vol.Required(CONF_HEATING_SYSTEM, default=defaults[CONF_HEATING_SYSTEM]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[
                        {"value": CONF_RADIATOR_HIGH_TEMPERATURES, "label": "Radiators ( High Temperatures )"},
                        {"value": CONF_RADIATOR_LOW_TEMPERATURES, "label": "Radiators ( Low Temperatures )"},
                        {"value": CONF_UNDERFLOOR, "label": "Underfloor"}
                    ])
                ),
                vol.Required(CONF_PROPORTIONAL, default=defaults.get(CONF_PROPORTIONAL)): str,
                vol.Required(CONF_INTEGRAL, default=defaults.get(CONF_INTEGRAL)): str,
                vol.Required(CONF_DERIVATIVE, default=defaults.get(CONF_DERIVATIVE)): str,
            })
        )

    async def async_step_presets(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            return await self.update_options(_user_input)

        defaults = await self.get_options()
        return self.async_show_form(
            step_id="presets",
            data_schema=vol.Schema({
                vol.Required(CONF_AWAY_TEMPERATURE, default=defaults[CONF_AWAY_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5)
                ),
                vol.Required(CONF_SLEEP_TEMPERATURE, default=defaults[CONF_SLEEP_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5)
                ),
                vol.Required(CONF_HOME_TEMPERATURE, default=defaults[CONF_HOME_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5)
                ),
                vol.Required(CONF_COMFORT_TEMPERATURE, default=defaults[CONF_COMFORT_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5)
                ),
            })
        )

    async def async_step_climates(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            if _user_input.get(CONF_MAIN_CLIMATES) is None:
                self._options[CONF_MAIN_CLIMATES] = []

            if _user_input.get(CONF_CLIMATES) is None:
                self._options[CONF_CLIMATES] = []

            return await self.update_options(_user_input)

        defaults = await self.get_options()
        return self.async_show_form(
            step_id="climates",
            data_schema=vol.Schema({
                vol.Optional(CONF_MAIN_CLIMATES, default=defaults[CONF_MAIN_CLIMATES]): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN, multiple=True)
                ),
                vol.Optional(CONF_CLIMATES, default=defaults[CONF_CLIMATES]): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN, multiple=True)
                ),
            })
        )

    async def async_step_advanced(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            return await self.update_options(_user_input)

        defaults = await self.get_options()
        return self.async_show_form(
            step_id="advanced",
            data_schema=vol.Schema({
                vol.Required(CONF_SIMULATION, default=defaults[CONF_SIMULATION]): bool,
                vol.Required(CONF_OVERSHOOT_PROTECTION, default=defaults[CONF_OVERSHOOT_PROTECTION]): bool,
                vol.Required(CONF_MIN_NUM_UPDATES, default=defaults[CONF_MIN_NUM_UPDATES]): int,
                vol.Required(CONF_SAMPLE_TIME, default=defaults.get(CONF_SAMPLE_TIME)): selector.TimeSelector(),
                vol.Required(CONF_SENSOR_MAX_VALUE_AGE, default=defaults.get(CONF_SENSOR_MAX_VALUE_AGE)): selector.TimeSelector(),
            })
        )

    async def update_options(self, _user_input) -> FlowResult:
        self._options.update(_user_input)
        return self.async_create_entry(title=self._config_entry.data[CONF_NAME], data=self._options)

    async def get_options(self):
        defaults = OPTIONS_DEFAULTS.copy()
        defaults.update(self._options)

        return defaults
