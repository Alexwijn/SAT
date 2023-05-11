"""Adds config flow for SAT."""
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import mqtt
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN, BinarySensorDeviceClass
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.dhcp import DhcpServiceInfo
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector, entity_registry
from pyotgw import OpenThermGateway

from .const import *

DEFAULT_NAME = "Living Room"

_LOGGER = logging.getLogger(__name__)


class SatFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for SAT."""
    VERSION = 1

    def __init__(self):
        """Initialize."""
        self._data = {}
        self._errors = {}

    async def async_step_user(self, _user_input=None) -> FlowResult:
        """Handle user flow."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["mqtt", "serial", "switch"]
        )

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> FlowResult:
        """Handle dhcp discovery."""
        _LOGGER.debug("Discovered OTGW at [%s]", discovery_info.hostname)
        self._data[CONF_DEVICE] = f"socket://{discovery_info.hostname}:25238"

        # abort if we already have exactly this gateway id/host
        # reload the integration if the host got updated
        await self.async_set_unique_id(discovery_info.hostname)
        self._abort_if_unique_id_configured(updates=self._data, reload_on_update=True)

        return await self.async_step_serial()

    async def async_step_mqtt(self, _user_input=None):
        self._errors = {}

        if _user_input is not None:
            self._data.update(_user_input)
            self._data[CONF_MODE] = MODE_MQTT

            if not await mqtt.async_wait_for_mqtt_client(self.hass):
                self._errors["base"] = "mqtt_component"
                return await self.async_step_mqtt()

            await self.async_set_unique_id(self._data[CONF_DEVICE], raise_on_progress=False)
            self._abort_if_unique_id_configured()

            return await self.async_step_sensors_setup()

        return self.async_show_form(
            step_id="mqtt",
            last_step=False,
            errors=self._errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_MQTT_TOPIC, default=OPTIONS_DEFAULTS[CONF_MQTT_TOPIC]): str,
                vol.Required(CONF_DEVICE): selector.DeviceSelector(
                    selector.DeviceSelectorConfig(model="otgw-nodo")
                ),
            }),
        )

    async def async_step_serial(self, _user_input=None):
        self._errors = {}

        if _user_input is not None:
            self._data.update(_user_input)
            self._data[CONF_MODE] = MODE_SERIAL

            if not await OpenThermGateway().connect(port=self._data[CONF_DEVICE], skip_init=True, timeout=5):
                self._errors["base"] = "connection"
                return await self.async_step_serial()

            await self.async_set_unique_id(self._data[CONF_DEVICE], raise_on_progress=False)
            self._abort_if_unique_id_configured()

            return await self.async_step_sensors_setup()

        return self.async_show_form(
            step_id="serial",
            last_step=False,
            errors=self._errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_DEVICE, default="socket://otgw.local:25238"): str,
            }),
        )

    async def async_step_switch(self, _user_input=None):
        if _user_input is not None:
            self._data.update(_user_input)
            self._data[CONF_MODE] = MODE_SWITCH

            await self.async_set_unique_id(self._data[CONF_DEVICE], raise_on_progress=False)

            self._abort_if_unique_id_configured()

            return await self.async_step_sensors_setup()

        return self.async_show_form(
            step_id="switch",
            last_step=False,
            errors=self._errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_DEVICE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=[SWITCH_DOMAIN])
                )
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
        menu_options = ["general", "presets", "climates", "contact_sensors"]

        if self.show_advanced_options:
            menu_options.append("advanced")

        return self.async_show_menu(
            step_id="user",
            menu_options=menu_options
        )

    async def async_step_general(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            return await self.update_options(_user_input)

        options = await self.get_options()

        schema = {
            vol.Required(CONF_HEATING_CURVE_COEFFICIENT, default=options[CONF_HEATING_CURVE_COEFFICIENT]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.1, max=12, step=0.1)
            ),
            vol.Required(CONF_TARGET_TEMPERATURE_STEP, default=options[CONF_TARGET_TEMPERATURE_STEP]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.1, max=1, step=0.05)
            ),
        }

        if options.get(CONF_MODE) in [MODE_MQTT, MODE_SERIAL]:
            schema[vol.Required(CONF_HEATING_SYSTEM, default=options[CONF_HEATING_SYSTEM])] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=[
                    {"value": HEATING_SYSTEM_RADIATOR_HIGH_TEMPERATURES, "label": "Radiators ( High Temperatures )"},
                    {"value": HEATING_SYSTEM_RADIATOR_MEDIUM_TEMPERATURES, "label": "Radiators ( Medium Temperatures )"},
                    {"value": HEATING_SYSTEM_RADIATOR_LOW_TEMPERATURES, "label": "Radiators ( Low Temperatures )"},
                    {"value": HEATING_SYSTEM_UNDERFLOOR, "label": "Underfloor"}
                ])
            )

        if options.get(CONF_MODE) == MODE_SWITCH:
            schema[vol.Required(CONF_SETPOINT, default=50)] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1)
            )

        if not options.get(CONF_AUTOMATIC_GAINS):
            schema[vol.Required(CONF_PROPORTIONAL, default=options.get(CONF_PROPORTIONAL))] = str
            schema[vol.Required(CONF_INTEGRAL, default=options.get(CONF_INTEGRAL))] = str
            schema[vol.Required(CONF_DERIVATIVE, default=options.get(CONF_DERIVATIVE))] = str

        if not options.get(CONF_AUTOMATIC_DUTY_CYCLE):
            schema[vol.Required(CONF_DUTY_CYCLE, default=options.get(CONF_DUTY_CYCLE))] = selector.TimeSelector()

        return self.async_show_form(step_id="general", data_schema=vol.Schema(schema))

    async def async_step_presets(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            return await self.update_options(_user_input)

        defaults = await self.get_options()
        return self.async_show_form(
            step_id="presets",
            data_schema=vol.Schema({
                vol.Required(CONF_ACTIVITY_TEMPERATURE, default=defaults[CONF_ACTIVITY_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5)
                ),
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
                vol.Required(CONF_SYNC_CLIMATES_WITH_PRESET, default=defaults[CONF_SYNC_CLIMATES_WITH_PRESET]): bool,
            })
        )

    async def async_step_climates(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            if _user_input.get(CONF_MAIN_CLIMATES) is None:
                self._options[CONF_MAIN_CLIMATES] = []

            if _user_input.get(CONF_CLIMATES) is None:
                self._options[CONF_CLIMATES] = []

            return await self.update_options(_user_input)

        entities = entity_registry.async_get(self.hass)
        device_name = self._config_entry.data.get(CONF_NAME)
        climate_id = entities.async_get_entity_id(CLIMATE_DOMAIN, DOMAIN, str(device_name).lower())

        entity_selector = selector.EntitySelector(selector.EntitySelectorConfig(
            exclude_entities=[climate_id], domain=CLIMATE_DOMAIN, multiple=True
        ))

        options = await self.get_options()
        return self.async_show_form(
            step_id="climates",
            data_schema=vol.Schema({
                vol.Optional(CONF_MAIN_CLIMATES, default=options[CONF_MAIN_CLIMATES]): entity_selector,
                vol.Optional(CONF_CLIMATES, default=options[CONF_CLIMATES]): entity_selector,
                vol.Required(CONF_SYNC_WITH_THERMOSTAT, default=options[CONF_SYNC_WITH_THERMOSTAT]): bool,
            })
        )

    async def async_step_contact_sensors(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            return await self.update_options(_user_input)

        defaults = await self.get_options()
        device_class = [BinarySensorDeviceClass.WINDOW, BinarySensorDeviceClass.DOOR, BinarySensorDeviceClass.GARAGE_DOOR]

        return self.async_show_form(
            step_id="contact_sensors",
            data_schema=vol.Schema({
                vol.Optional(CONF_WINDOW_SENSOR, default=defaults[CONF_WINDOW_SENSOR]): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=BINARY_SENSOR_DOMAIN, device_class=device_class)
                ),
            })
        )

    async def async_step_advanced(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            return await self.update_options(_user_input)

        options = await self.get_options()

        schema = {
            vol.Required(CONF_SIMULATION, default=options[CONF_SIMULATION]): bool,
            vol.Required(CONF_AUTOMATIC_GAINS, default=options.get(CONF_AUTOMATIC_GAINS)): bool,
            vol.Required(CONF_AUTOMATIC_DUTY_CYCLE, default=options.get(CONF_AUTOMATIC_DUTY_CYCLE)): bool,
        }

        if options.get(CONF_MODE) in [MODE_MQTT, MODE_SERIAL]:
            schema[vol.Required(CONF_FORCE_PULSE_WIDTH_MODULATION, default=options[CONF_FORCE_PULSE_WIDTH_MODULATION])] = bool
            schema[vol.Required(CONF_OVERSHOOT_PROTECTION, default=options[CONF_OVERSHOOT_PROTECTION])] = bool

        schema[vol.Required(CONF_SAMPLE_TIME, default=options.get(CONF_SAMPLE_TIME))] = selector.TimeSelector()
        schema[vol.Required(CONF_SENSOR_MAX_VALUE_AGE, default=options.get(CONF_SENSOR_MAX_VALUE_AGE))] = selector.TimeSelector()
        schema[vol.Required(CONF_WINDOW_MINIMUM_OPEN_TIME, default=options.get(CONF_WINDOW_MINIMUM_OPEN_TIME))] = selector.TimeSelector()

        schema[vol.Required(CONF_CLIMATE_VALVE_OFFSET, default=options[CONF_CLIMATE_VALVE_OFFSET])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=-1, max=1, step=0.1)
        )

        return self.async_show_form(
            step_id="advanced",
            data_schema=vol.Schema(schema)
        )

    async def update_options(self, _user_input) -> FlowResult:
        self._options.update(_user_input)
        return self.async_create_entry(title=self._config_entry.data[CONF_NAME], data=self._options)

    async def get_options(self):
        defaults = OPTIONS_DEFAULTS.copy()
        defaults.update(self._options)

        return defaults
