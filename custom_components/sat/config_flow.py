"""Adds config flow for SAT."""
import asyncio
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import mqtt
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN, BinarySensorDeviceClass
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.dhcp import DhcpServiceInfo
from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.mqtt import DOMAIN as MQTT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.config_entries import ConfigEntry, SOURCE_USER
from homeassistant.const import MAJOR_VERSION, MINOR_VERSION
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector, entity_registry, device_registry
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
from pyotgw import OpenThermGateway

from . import SatDataUpdateCoordinatorFactory
from .const import *
from .coordinator import SatDataUpdateCoordinator
from .overshoot_protection import OvershootProtection
from .util import calculate_default_maximum_setpoint

DEFAULT_NAME = "Living Room"

_LOGGER = logging.getLogger(__name__)


class SatFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for SAT."""
    VERSION = 2
    calibration = None
    overshoot_protection_value = None

    def __init__(self):
        """Initialize."""
        self._data = {}
        self._errors = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return SatOptionsFlowHandler(config_entry)

    @callback
    def async_remove(self) -> None:
        if self.calibration is not None:
            self.calibration.cancel()

    async def async_step_user(self, _user_input=None) -> FlowResult:
        """Handle user flow."""
        menu_options = []

        # Since we rely on the availability logic in 2023.5, we do not support below it.
        if MAJOR_VERSION >= 2023 and MINOR_VERSION >= 5:
            menu_options.append("mosquitto")

        menu_options.append("serial")
        menu_options.append("switch")

        if self.show_advanced_options:
            menu_options.append("simulator")

        return self.async_show_menu(step_id="user", menu_options=menu_options)

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> FlowResult:
        """Handle dhcp discovery."""
        _LOGGER.debug("Discovered OTGW at [socket://%s]", discovery_info.hostname)
        self._data[CONF_DEVICE] = f"socket://{discovery_info.hostname}:25238"

        # abort if we already have exactly this gateway id/host
        # reload the integration if the host got updated
        await self.async_set_unique_id(discovery_info.hostname)
        self._abort_if_unique_id_configured(updates=self._data, reload_on_update=True)

        return await self.async_step_serial()

    async def async_step_mqtt(self, discovery_info: MqttServiceInfo):
        """Handle dhcp discovery."""
        device = device_registry.async_get(self.hass).async_get_device(
            {(MQTT_DOMAIN, discovery_info.topic[11:])}
        )

        _LOGGER.debug("Discovered OTGW at [mqtt://%s]", discovery_info.topic)
        self._data[CONF_DEVICE] = device.id

        # abort if we already have exactly this gateway id/host
        # reload the integration if the host got updated
        await self.async_set_unique_id(device.id)
        self._abort_if_unique_id_configured(updates=self._data, reload_on_update=True)

        return await self.async_step_mosquitto()

    async def async_step_mosquitto(self, _user_input=None):
        self._errors = {}

        if _user_input is not None:
            self._data.update(_user_input)
            self._data[CONF_MODE] = MODE_MQTT

            if not await mqtt.async_wait_for_mqtt_client(self.hass):
                self._errors["base"] = "mqtt_component"
                return await self.async_step_mosquitto()

            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="mosquitto",
            last_step=False,
            errors=self._errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_MQTT_TOPIC, default=OPTIONS_DEFAULTS[CONF_MQTT_TOPIC]): str,
                vol.Required(CONF_DEVICE, default=self._data.get(CONF_DEVICE)): selector.DeviceSelector(
                    selector.DeviceSelectorConfig(model="otgw-nodo")
                ),
            }),
        )

    async def async_step_serial(self, _user_input=None):
        self._errors = {}

        if _user_input is not None:
            self._data.update(_user_input)
            self._data[CONF_MODE] = MODE_SERIAL

            gateway = OpenThermGateway()
            if not await gateway.connect(port=self._data[CONF_DEVICE], skip_init=True, timeout=5):
                await gateway.disconnect()
                self._errors["base"] = "connection"
                return await self.async_step_serial()

            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="serial",
            last_step=False,
            errors=self._errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_DEVICE, default=self._data.get(CONF_DEVICE, "socket://otgw.local:25238")): str,
            }),
        )

    async def async_step_switch(self, _user_input=None):
        if _user_input is not None:
            self._data.update(_user_input)
            self._data[CONF_MODE] = MODE_SWITCH

            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="switch",
            last_step=False,
            errors=self._errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_DEVICE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=[SWITCH_DOMAIN, INPUT_BOOLEAN_DOMAIN])
                ),
                vol.Required(CONF_MINIMUM_SETPOINT, default=50): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=100, step=1)
                )
            }),
        )

    async def async_step_simulator(self, _user_input=None):
        if _user_input is not None:
            self._data.update(_user_input)
            self._data[CONF_MODE] = MODE_SIMULATOR
            self._data[CONF_DEVICE] = MODE_SIMULATOR

            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="simulator",
            last_step=False,
            errors=self._errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_SIMULATED_HEATING, default=OPTIONS_DEFAULTS[CONF_SIMULATED_HEATING]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=100, step=1)
                ),
                vol.Required(CONF_SIMULATED_COOLING, default=OPTIONS_DEFAULTS[CONF_SIMULATED_COOLING]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=100, step=1)
                ),
                vol.Required(CONF_MINIMUM_SETPOINT, default=OPTIONS_DEFAULTS[CONF_MINIMUM_SETPOINT]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=100, step=1)
                ),
                vol.Required(CONF_SIMULATED_WARMING_UP, default=OPTIONS_DEFAULTS[CONF_SIMULATED_WARMING_UP]): selector.TimeSelector()
            }),
        )

    async def async_step_sensors(self, _user_input=None):
        await self.async_set_unique_id(self._data[CONF_DEVICE], raise_on_progress=False)
        self._abort_if_unique_id_configured()

        if _user_input is not None:
            self._data.update(_user_input)

            if self._data[CONF_MODE] in [MODE_MQTT, MODE_SERIAL, MODE_SIMULATOR]:
                return await self.async_step_heating_system()

            return await self.async_step_pid_controller()

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

    async def async_step_heating_system(self, _user_input=None):
        if _user_input is not None:
            self._data.update(_user_input)

            if (await self._create_coordinator()).supports_setpoint_management:
                return await self.async_step_automatic_gains()

            return await self.async_step_finish()

        return self.async_show_form(
            last_step=False,
            step_id="heating_system",
            data_schema=vol.Schema({
                vol.Required(CONF_HEATING_SYSTEM, default=OPTIONS_DEFAULTS[CONF_HEATING_SYSTEM]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[
                        {"value": HEATING_SYSTEM_RADIATORS, "label": "Radiators"},
                        {"value": HEATING_SYSTEM_UNDERFLOOR, "label": "Underfloor"}
                    ])
                )
            })
        )

    async def async_step_automatic_gains(self, _user_input=None):
        return self.async_show_menu(
            step_id="automatic_gains",
            menu_options=["calibrate", "overshoot_protection", "pid_controller"]
        )

    async def async_step_calibrate(self, _user_input=None):
        coordinator = await self._create_coordinator()

        async def start_calibration():
            try:
                overshoot_protection = OvershootProtection(coordinator)
                self.overshoot_protection_value = await overshoot_protection.calculate()
            except asyncio.CancelledError:
                _LOGGER.warning("Cancelled calibration.")
                return False

            self.hass.async_create_task(
                self.hass.config_entries.flow.async_configure(flow_id=self.flow_id)
            )

            return True

        if not self.calibration:
            self.calibration = self.hass.async_create_task(
                start_calibration()
            )

            return self.async_show_progress(
                step_id="calibrate",
                progress_action="calibration",
            )

        if self.overshoot_protection_value is None:
            return self.async_abort(reason="unable_to_calibrate")

        await self._enable_overshoot_protection(
            self.overshoot_protection_value
        )

        self.calibration = None
        self.overshoot_protection_value = None

        return self.async_show_progress_done(next_step_id="calibrated")

    async def async_step_calibrated(self, _user_input=None):
        return self.async_show_menu(
            step_id="calibrated",
            description_placeholders=self._data,
            menu_options=["calibrate", "finish"],
        )

    async def async_step_overshoot_protection(self, _user_input=None):
        if _user_input is not None:
            await self._enable_overshoot_protection(
                _user_input[CONF_MINIMUM_SETPOINT]
            )

            return await self.async_step_finish()

        return self.async_show_form(
            step_id="overshoot_protection",
            data_schema=vol.Schema({
                vol.Required(CONF_MINIMUM_SETPOINT, default=OPTIONS_DEFAULTS[CONF_MINIMUM_SETPOINT]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=MINIMUM_SETPOINT, max=OVERSHOOT_PROTECTION_SETPOINT, step=1, unit_of_measurement="°C")
                ),
            })
        )

    async def async_step_pid_controller(self, _user_input=None):
        self._data[CONF_AUTOMATIC_GAINS] = False

        if _user_input is not None:
            self._data.update(_user_input)
            return await self.async_step_finish()

        return self.async_show_form(
            step_id="pid_controller",
            data_schema=vol.Schema({
                vol.Required(CONF_PROPORTIONAL, default=OPTIONS_DEFAULTS[CONF_PROPORTIONAL]): str,
                vol.Required(CONF_INTEGRAL, default=OPTIONS_DEFAULTS[CONF_INTEGRAL]): str,
                vol.Required(CONF_DERIVATIVE, default=OPTIONS_DEFAULTS[CONF_DERIVATIVE]): str
            })
        )

    async def async_step_finish(self, _user_input=None):
        return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)

    async def _create_coordinator(self) -> SatDataUpdateCoordinator:
        # Create a new config to use
        config = ConfigEntry(
            version=self.VERSION, domain=DOMAIN, title=self._data[CONF_NAME], data=self._data, source=SOURCE_USER
        )

        # Resolve the coordinator by using the factory according to the mode
        return await SatDataUpdateCoordinatorFactory().resolve(
            hass=self.hass, config_entry=config, mode=self._data[CONF_MODE], device=self._data[CONF_DEVICE]
        )

    async def _enable_overshoot_protection(self, overshoot_protection_value: float):
        self._data[CONF_OVERSHOOT_PROTECTION] = True
        self._data[CONF_MINIMUM_SETPOINT] = overshoot_protection_value


class SatOptionsFlowHandler(config_entries.OptionsFlow):
    """Config flow options handler."""

    def __init__(self, config_entry: ConfigEntry):
        self._config_entry = config_entry
        self._options = dict(config_entry.options)

    async def async_step_init(self, _user_input=None):
        menu_options = ["general", "areas", "presets", "system_configuration"]

        if self.show_advanced_options:
            menu_options.append("advanced")

        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options
        )

    async def async_step_general(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            return await self.update_options(_user_input)

        schema = {}
        options = await self.get_options()

        default_maximum_setpoint = calculate_default_maximum_setpoint(options.get(CONF_HEATING_SYSTEM))
        maximum_setpoint = float(options.get(CONF_MAXIMUM_SETPOINT, default_maximum_setpoint))

        schema[vol.Required(CONF_MAXIMUM_SETPOINT, default=maximum_setpoint)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=10, max=100, step=1, unit_of_measurement="°C")
        )

        if not options.get(CONF_AUTOMATIC_GAINS):
            schema[vol.Required(CONF_PROPORTIONAL, default=options.get(CONF_PROPORTIONAL))] = str
            schema[vol.Required(CONF_INTEGRAL, default=options.get(CONF_INTEGRAL))] = str
            schema[vol.Required(CONF_DERIVATIVE, default=options.get(CONF_DERIVATIVE))] = str

        schema[vol.Required(CONF_HEATING_CURVE_COEFFICIENT, default=options[CONF_HEATING_CURVE_COEFFICIENT])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.1, max=12, step=0.1)
        )

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
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
                ),
                vol.Required(CONF_AWAY_TEMPERATURE, default=defaults[CONF_AWAY_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
                ),
                vol.Required(CONF_SLEEP_TEMPERATURE, default=defaults[CONF_SLEEP_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
                ),
                vol.Required(CONF_HOME_TEMPERATURE, default=defaults[CONF_HOME_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
                ),
                vol.Required(CONF_COMFORT_TEMPERATURE, default=defaults[CONF_COMFORT_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
                ),
                vol.Required(CONF_SYNC_CLIMATES_WITH_PRESET, default=defaults[CONF_SYNC_CLIMATES_WITH_PRESET]): bool,
            })
        )

    async def async_step_areas(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            if _user_input.get(CONF_MAIN_CLIMATES) is None:
                self._options[CONF_MAIN_CLIMATES] = []

            if _user_input.get(CONF_CLIMATES) is None:
                self._options[CONF_CLIMATES] = []

            if _user_input.get(CONF_WINDOW_SENSOR) is None:
                self._options[CONF_WINDOW_SENSOR] = []

            return await self.update_options(_user_input)

        entities = entity_registry.async_get(self.hass)
        device_name = self._config_entry.data.get(CONF_NAME)
        climate_id = entities.async_get_entity_id(CLIMATE_DOMAIN, DOMAIN, str(device_name).lower())

        climate_selector = selector.EntitySelector(selector.EntitySelectorConfig(
            exclude_entities=[climate_id], domain=CLIMATE_DOMAIN, multiple=True
        ))

        contact_sensor_selector = selector.EntitySelector(selector.EntitySelectorConfig(
            domain=BINARY_SENSOR_DOMAIN, device_class=[
                BinarySensorDeviceClass.DOOR,
                BinarySensorDeviceClass.WINDOW,
                BinarySensorDeviceClass.GARAGE_DOOR
            ])
        )

        options = await self.get_options()

        return self.async_show_form(
            step_id="areas",
            data_schema=vol.Schema({
                vol.Optional(CONF_MAIN_CLIMATES, default=options[CONF_MAIN_CLIMATES]): climate_selector,
                vol.Optional(CONF_CLIMATES, default=options[CONF_CLIMATES]): climate_selector,
                vol.Optional(CONF_WINDOW_SENSOR, default=options[CONF_WINDOW_SENSOR]): contact_sensor_selector,
                vol.Required(CONF_SYNC_WITH_THERMOSTAT, default=options[CONF_SYNC_WITH_THERMOSTAT]): bool,
            })
        )

    async def async_step_system_configuration(self, _user_input=None) -> FlowResult:
        if _user_input is not None:
            return await self.update_options(_user_input)

        options = await self.get_options()

        return self.async_show_form(
            step_id="system_configuration",
            data_schema=vol.Schema({
                vol.Required(CONF_AUTOMATIC_DUTY_CYCLE, default=options.get(CONF_AUTOMATIC_DUTY_CYCLE)): bool,
                vol.Required(CONF_SENSOR_MAX_VALUE_AGE, default=options.get(CONF_SENSOR_MAX_VALUE_AGE)): selector.TimeSelector(),
                vol.Required(CONF_WINDOW_MINIMUM_OPEN_TIME, default=options.get(CONF_WINDOW_MINIMUM_OPEN_TIME)): selector.TimeSelector(),
            })
        )

    async def async_step_advanced(self, _user_input=None) -> FlowResult:
        options = await self.get_options()
        if _user_input is not None:
            return await self.update_options(_user_input)

        schema = {}
        schema[vol.Required(CONF_SIMULATION, default=options[CONF_SIMULATION])]: bool

        if options.get(CONF_MODE) in [MODE_MQTT, MODE_SERIAL, MODE_SIMULATOR]:
            schema[vol.Required(CONF_FORCE_PULSE_WIDTH_MODULATION, default=options[CONF_FORCE_PULSE_WIDTH_MODULATION])] = bool

        schema[vol.Required(CONF_CLIMATE_VALVE_OFFSET, default=options[CONF_CLIMATE_VALVE_OFFSET])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=-1, max=1, step=0.1)
        )

        schema[vol.Required(CONF_TARGET_TEMPERATURE_STEP, default=options[CONF_TARGET_TEMPERATURE_STEP])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.1, max=1, step=0.05)
        )

        schema[vol.Required(CONF_SAMPLE_TIME, default=options.get(CONF_SAMPLE_TIME))] = selector.TimeSelector()

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
