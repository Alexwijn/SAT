"""Adds config flow for SAT."""
import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import sensor, switch, valve, weather, binary_sensor, climate, input_boolean
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import callback
from homeassistant.helpers import selector, entity_registry
from homeassistant.helpers.selector import SelectSelectorMode, SelectOptionDict
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
from pyotgw import OpenThermGateway
from voluptuous import Marker

from . import SatDataUpdateCoordinatorFactory
from .const import *
from .coordinator import SatDataUpdateCoordinator
from .helpers import calculate_default_maximum_setpoint, snake_case
from .manufacturer import ManufacturerFactory, MANUFACTURERS
from .overshoot_protection import OvershootProtection
from .validators import valid_serial_device

DEFAULT_NAME = "Living Room"

_LOGGER = logging.getLogger(__name__)


class SatFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for SAT."""
    VERSION = 10
    MINOR_VERSION = 0

    calibration = None
    previous_hvac_mode = None
    overshoot_protection_value = None

    def __init__(self):
        """Initialize."""
        self.data = {}
        self.errors = {}
        self.config_entry = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return SatOptionsFlowHandler(config_entry)

    @callback
    def async_remove(self) -> None:
        if self.calibration is not None:
            self.calibration.cancel()

    async def async_step_user(self, _user_input: dict[str, Any] | None = None):
        """Handle user flow."""
        menu_options = [
            "mosquitto",
            "esphome",
            "serial",
            "switch"
        ]

        if self.show_advanced_options:
            menu_options.append("simulator")

        return self.async_show_menu(step_id="user", menu_options=menu_options)

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo):
        """Handle dhcp discovery."""
        _LOGGER.debug("Discovered OTGW at [socket://%s]", discovery_info.hostname)
        self.data[CONF_DEVICE] = f"socket://{discovery_info.hostname}:25238"

        # abort if we already have exactly this gateway id/host
        # reload the integration if the host got updated
        await self.async_set_unique_id(discovery_info.hostname)
        self._abort_if_unique_id_configured(updates=self.data)

        return await self.async_step_serial()

    async def async_step_mqtt(self, discovery_info: MqttServiceInfo):
        """Handle mqtt discovery."""
        _LOGGER.debug("Discovered MQTT at [mqtt://%s]", discovery_info.topic)

        # Mapping topic prefixes to handler methods and device IDs
        topic_mapping = {
            "ems-esp/": (MODE_MQTT_EMS, "ems-esp", self.async_step_mosquitto_ems),
            "OTGW/": (MODE_MQTT_OPENTHERM, discovery_info.topic[11:], self.async_step_mosquitto_opentherm),
        }

        # Check for matching prefix and handle appropriately
        for prefix, (mode, device_id, step_method) in topic_mapping.items():
            if discovery_info.topic.startswith(prefix):
                _LOGGER.debug("Identified gateway type %s: %s", mode[5:], device_id)
                self.data[CONF_MODE] = mode
                self.data[CONF_DEVICE] = device_id

                # Abort if the gateway is already registered, reload if necessary
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured(updates=self.data)

                return await step_method()

        _LOGGER.error("Unsupported MQTT topic format: %s", discovery_info.topic)
        return self.async_abort(reason="unsupported_gateway")

    async def async_step_mosquitto(self, _user_input: dict[str, Any] | None = None):
        """Entry step to select the MQTT mode and branch to a specific setup."""

        if _user_input is not None:
            self.errors = {}
            self.data.update(_user_input)

            if self.data[CONF_MODE] == MODE_MQTT_OPENTHERM:
                return await self.async_step_mosquitto_opentherm()

            if self.data[CONF_MODE] == MODE_MQTT_EMS:
                return await self.async_step_mosquitto_ems()

        return self.async_show_form(
            step_id="mosquitto",
            last_step=False,
            errors=self.errors,
            data_schema=vol.Schema({
                vol.Required(CONF_MODE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        mode=SelectSelectorMode.DROPDOWN,
                        options=[
                            selector.SelectOptionDict(value=MODE_MQTT_OPENTHERM, label="OpenTherm Gateway (For advanced boiler control)"),
                            selector.SelectOptionDict(value=MODE_MQTT_EMS, label="EMS-ESP (For Bosch, Junkers, Buderus systems)"),
                        ]
                    )
                ),
            }),
        )

    async def async_step_mosquitto_opentherm(self, _user_input: dict[str, Any] | None = None):
        """Setup specific to OpenTherm Gateway."""
        if _user_input is not None:
            self.data.update(_user_input)

            return await self.async_step_sensors()

        return self._create_mqtt_form("mosquitto_opentherm", "OTGW", "otgw-XXXXXXXXXXXX")

    async def async_step_mosquitto_ems(self, _user_input: dict[str, Any] | None = None):
        """Setup specific to EMS-ESP."""
        if _user_input is not None:
            self.data.update(_user_input)
            self.data[CONF_DEVICE] = "ems-esp"

            return await self.async_step_sensors()

        return self._create_mqtt_form("mosquitto_ems", "ems-esp")

    async def async_step_esphome(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)
            self.data[CONF_MODE] = MODE_ESPHOME

            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="esphome",
            last_step=False,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_DEVICE, default=self.data.get(CONF_DEVICE)): selector.DeviceSelector(
                    selector.DeviceSelectorConfig(integration="esphome")
                ),
            }),
        )

    async def async_step_serial(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.errors = {}
            self.data.update(_user_input)
            self.data[CONF_MODE] = MODE_SERIAL

            if not valid_serial_device(self.data[CONF_DEVICE]):
                self.errors["base"] = "invalid_device"
                return await self.async_step_serial()

            gateway = OpenThermGateway()

            try:
                connected = await asyncio.wait_for(gateway.connection.connect(port=self.data[CONF_DEVICE]), timeout=5)
            except asyncio.TimeoutError:
                connected = False

            if not connected:
                self.errors["base"] = "connection"
                return await self.async_step_serial()

            await gateway.disconnect()
            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="serial",
            last_step=False,
            errors=self.errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_DEVICE, default=self.data.get(CONF_DEVICE, "socket://otgw.local:25238")): str,
            }),
        )

    async def async_step_switch(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)
            self.data[CONF_MODE] = MODE_SWITCH

            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="switch",
            last_step=False,
            errors=self.errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_DEVICE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=[switch.DOMAIN, valve.DOMAIN, input_boolean.DOMAIN])
                ),
                vol.Required(CONF_MINIMUM_SETPOINT, default=50): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=100, step=1)
                )
            }),
        )

    async def async_step_simulator(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)
            self.data[CONF_MODE] = MODE_SIMULATOR
            self.data[CONF_DEVICE] = f"%s_%s".format(MODE_SIMULATOR, snake_case(_user_input.get(CONF_NAME)))

            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="simulator",
            last_step=False,
            errors=self.errors,
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

    async def async_step_reconfigure(self, _user_input: dict[str, Any] | None = None):
        self.config_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self.data = self.config_entry.data.copy()

        return await self.async_step_sensors()

    async def async_step_sensors(self, _user_input: dict[str, Any] | None = None):
        if self.config_entry is None:
            await self.async_set_unique_id(self.data[CONF_DEVICE], raise_on_progress=False)
            self._abort_if_unique_id_configured()

        if _user_input is not None:
            self.data.update(_user_input)

            if _user_input.get(CONF_HUMIDITY_SENSOR_ENTITY_ID) is None:
                self.data[CONF_HUMIDITY_SENSOR_ENTITY_ID] = None

            if self.data[CONF_MODE] in [MODE_ESPHOME, MODE_MQTT_OPENTHERM, MODE_MQTT_EMS, MODE_SERIAL, MODE_SIMULATOR]:
                return await self.async_step_heating_system()

            return await self.async_step_areas()

        return self.async_show_form(
            last_step=False,
            step_id="sensors",
            data_schema=self.add_suggested_values_to_schema(vol.Schema({
                vol.Required(CONF_INSIDE_SENSOR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=sensor.DOMAIN, device_class=[sensor.SensorDeviceClass.TEMPERATURE])
                ),
                vol.Required(CONF_OUTSIDE_SENSOR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=[sensor.DOMAIN, weather.DOMAIN], multiple=True)
                ),
                vol.Optional(CONF_HUMIDITY_SENSOR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=sensor.DOMAIN, device_class=[sensor.SensorDeviceClass.HUMIDITY])
                )
            }), self.data),
        )

    async def async_step_heating_system(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)

            return await self.async_step_areas()

        return self.async_show_form(
            last_step=False,
            step_id="heating_system",
            data_schema=vol.Schema({
                vol.Required(CONF_HEATING_SYSTEM, default=self.data.get(CONF_HEATING_SYSTEM, OPTIONS_DEFAULTS[CONF_HEATING_SYSTEM])): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[
                        SelectOptionDict(value=HEATING_SYSTEM_RADIATORS, label="Radiators"),
                        SelectOptionDict(value=HEATING_SYSTEM_HEAT_PUMP, label="Heat Pump"),
                        SelectOptionDict(value=HEATING_SYSTEM_UNDERFLOOR, label="Underfloor"),
                    ])
                )
            })
        )

    async def async_step_areas(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)

            if _user_input.get(CONF_THERMOSTAT) is None:
                self.data[CONF_THERMOSTAT] = None

            if (await self.async_create_coordinator()).supports_setpoint_management:
                return await self.async_step_calibrate_system()

            return await self.async_step_automatic_gains()

        return self.async_show_form(
            last_step=False,
            step_id="areas",
            data_schema=self.add_suggested_values_to_schema(vol.Schema({
                vol.Optional(CONF_THERMOSTAT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=climate.DOMAIN)
                ),
                vol.Optional(CONF_RADIATORS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=climate.DOMAIN, multiple=True)
                ),
                vol.Optional(CONF_ROOMS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=climate.DOMAIN, multiple=True)
                ),
            }), self.data)
        )

    async def async_step_automatic_gains(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)

            if not self.data[CONF_AUTOMATIC_GAINS]:
                return await self.async_step_pid_controller()

            if self.data[CONF_MODE] == MODE_SIMULATOR:
                return await self.async_step_finish()

            return await self.async_step_manufacturer()

        return self.async_show_form(
            last_step=False,
            step_id="automatic_gains",
            data_schema=vol.Schema({vol.Required(CONF_AUTOMATIC_GAINS, default=True): bool})
        )

    async def async_step_calibrate_system(self, _user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="calibrate_system",
            menu_options=["calibrate", "overshoot_protection", "pid_controller"]
        )

    async def async_step_calibrate(self, _user_input: dict[str, Any] | None = None):
        # Let's see if we have already been configured before
        device_name = self.data[CONF_NAME]
        entities = entity_registry.async_get(self.hass)
        climate_id = entities.async_get_entity_id(climate.DOMAIN, DOMAIN, device_name.lower())

        async def start_calibration():
            try:
                coordinator = await self.async_create_coordinator()
                await coordinator.async_setup()

                overshoot_protection = OvershootProtection(coordinator, self.data.get(CONF_HEATING_SYSTEM))
                self.overshoot_protection_value = await overshoot_protection.calculate()

                await coordinator.async_will_remove_from_hass()
            except asyncio.TimeoutError:
                _LOGGER.warning("Timed out during overshoot protection calculation.")
            except asyncio.CancelledError:
                _LOGGER.warning("Cancelled overshoot protection calculation.")

        if not self.calibration:
            self.calibration = self.hass.async_create_task(
                start_calibration()
            )

            # Make sure to turn off the existing climate if we found one
            if climate_id is not None:
                self.previous_hvac_mode = self.hass.states.get(climate_id).state
                data = {ATTR_ENTITY_ID: climate_id, climate.ATTR_HVAC_MODE: climate.HVACMode.OFF}
                await self.hass.services.async_call(climate.DOMAIN, climate.SERVICE_SET_HVAC_MODE, data, blocking=True)

            # Make sure all climate valves are open
            for entity_id in self.data.get(CONF_RADIATORS, []) + self.data.get(CONF_ROOMS, []):
                data = {ATTR_ENTITY_ID: entity_id, climate.ATTR_HVAC_MODE: climate.HVACMode.HEAT}
                await self.hass.services.async_call(climate.DOMAIN, climate.SERVICE_SET_HVAC_MODE, data, blocking=True)

            return self.async_show_progress(
                step_id="calibrate",
                progress_task=self.calibration,
                progress_action="calibration",
            )

        if self.overshoot_protection_value is None:
            return self.async_abort(reason="unable_to_calibrate")

        self._enable_overshoot_protection(
            self.overshoot_protection_value
        )

        self.calibration = None
        self.overshoot_protection_value = None

        # Make sure to restore the mode after we are done
        if climate_id is not None:
            data = {ATTR_ENTITY_ID: climate_id, climate.ATTR_HVAC_MODE: self.previous_hvac_mode}
            await self.hass.services.async_call(climate.DOMAIN, climate.SERVICE_SET_HVAC_MODE, data, blocking=True)

        return self.async_show_progress_done(next_step_id="calibrated")

    async def async_step_calibrated(self, _user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="calibrated",
            description_placeholders=self.data,
            menu_options=["calibrate", "finish"],
        )

    async def async_step_overshoot_protection(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self._enable_overshoot_protection(
                _user_input[CONF_MINIMUM_SETPOINT]
            )

            if self.data[CONF_MODE] == MODE_SIMULATOR:
                return await self.async_step_finish()

            return await self.async_step_manufacturer()

        return self.async_show_form(
            last_step=False,
            step_id="overshoot_protection",
            data_schema=vol.Schema({
                vol.Required(CONF_MINIMUM_SETPOINT, default=self.data.get(CONF_MINIMUM_SETPOINT, OPTIONS_DEFAULTS[CONF_MINIMUM_SETPOINT])): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=MINIMUM_SETPOINT, max=MAXIMUM_SETPOINT, step=1, unit_of_measurement="°C")
                ),
            })
        )

    async def async_step_pid_controller(self, _user_input: dict[str, Any] | None = None):
        self.data[CONF_AUTOMATIC_GAINS] = False

        if _user_input is not None:
            self.data.update(_user_input)

            if self.data[CONF_MODE] == MODE_SIMULATOR:
                return await self.async_step_finish()

            return await self.async_step_manufacturer()

        return self.async_show_form(
            last_step=False,
            step_id="pid_controller",
            data_schema=vol.Schema({
                vol.Required(CONF_PROPORTIONAL, default=self.data.get(CONF_PROPORTIONAL, OPTIONS_DEFAULTS[CONF_PROPORTIONAL])): str,
                vol.Required(CONF_INTEGRAL, default=self.data.get(CONF_INTEGRAL, OPTIONS_DEFAULTS[CONF_INTEGRAL])): str,
                vol.Required(CONF_DERIVATIVE, default=self.data.get(CONF_DERIVATIVE, OPTIONS_DEFAULTS[CONF_DERIVATIVE])): str
            })
        )

    async def async_step_manufacturer(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)
            return await self.async_step_finish()

        coordinator = await self.async_create_coordinator()
        await coordinator.async_setup()

        try:
            manufacturers = ManufacturerFactory.resolve_by_member_id(coordinator.member_id)
            default_manufacturer = manufacturers[0].friendly_name if len(manufacturers) > 0 else -1
        finally:
            await coordinator.async_will_remove_from_hass()

        options = []
        for name in MANUFACTURERS:
            manufacturer = ManufacturerFactory.resolve_by_name(name)
            options.append({"value": name, "label": manufacturer.friendly_name})

        return self.async_show_form(
            last_step=True,
            step_id="manufacturer",
            data_schema=vol.Schema({
                vol.Required(CONF_MANUFACTURER, default=default_manufacturer): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options)
                )
            })
        )

    async def async_step_finish(self, _user_input: dict[str, Any] | None = None):
        if self.config_entry is not None:
            return self.async_update_reload_and_abort(
                data=self.data,
                entry=self.config_entry,
                title=self.data[CONF_NAME],
                reason="reconfigure_successful",
            )

        return self.async_create_entry(
            title=self.data[CONF_NAME],
            data=self.data
        )

    async def async_create_coordinator(self) -> SatDataUpdateCoordinator:
        """Resolve the coordinator by using the factory according to the mode"""
        return SatDataUpdateCoordinatorFactory().resolve(
            hass=self.hass, data=self.data, mode=self.data[CONF_MODE], device=self.data[CONF_DEVICE]
        )

    def _create_mqtt_form(self, step_id: str, default_topic: Optional[str] = None, default_device: Optional[str] = None):
        """Create a common MQTT configuration form."""
        schema = {vol.Required(CONF_NAME, default=DEFAULT_NAME): str}

        if default_topic and not self.data.get(CONF_MQTT_TOPIC):
            schema[vol.Required(CONF_MQTT_TOPIC, default=default_topic)] = str

        if default_device and not self.data.get(CONF_DEVICE):
            schema[vol.Required(CONF_DEVICE, default=default_device)] = str

        return self.async_show_form(
            step_id=step_id,
            last_step=False,
            data_schema=vol.Schema(schema),
        )

    def _enable_overshoot_protection(self, overshoot_protection_value: float):
        """Store the value and enable overshoot protection."""
        self.data[CONF_OVERSHOOT_PROTECTION] = True
        self.data[CONF_MINIMUM_SETPOINT] = overshoot_protection_value


class SatOptionsFlowHandler(config_entries.OptionsFlow):
    """Config flow options handler."""

    def __init__(self, config_entry: ConfigEntry):
        self._config_entry = config_entry
        self._options = dict(config_entry.options)

    async def async_step_init(self, _user_input: dict[str, Any] | None = None):
        menu_options = ["general", "presets"]

        if len(self._config_entry.data.get(CONF_ROOMS, [])) > 0:
            menu_options.append("areas")

        menu_options.append("system_configuration")

        if self.show_advanced_options:
            menu_options.append("advanced")

        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options
        )

    async def async_step_general(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            return await self.update_options(_user_input)

        schema = {}
        options = await self.get_options()

        default_maximum_setpoint = calculate_default_maximum_setpoint(self._config_entry.data.get(CONF_HEATING_SYSTEM))
        maximum_setpoint = float(options.get(CONF_MAXIMUM_SETPOINT, default_maximum_setpoint))

        if len(self._config_entry.data.get(CONF_ROOMS, [])) > 0:
            schema[vol.Required(CONF_HEATING_MODE, default=str(options[CONF_HEATING_MODE]))] = selector.SelectSelector(
                selector.SelectSelectorConfig(mode=SelectSelectorMode.DROPDOWN, options=[
                    selector.SelectOptionDict(value=HEATING_MODE_COMFORT, label="Comfort"),
                    selector.SelectOptionDict(value=HEATING_MODE_ECO, label="Eco"),
                ])
            )

        schema[vol.Required(CONF_MAXIMUM_SETPOINT, default=maximum_setpoint)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=10, max=100, step=1, unit_of_measurement="°C")
        )

        schema[vol.Required(CONF_HEATING_CURVE_COEFFICIENT, default=options[CONF_HEATING_CURVE_COEFFICIENT])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.1, max=12, step=0.1)
        )

        if not options[CONF_AUTOMATIC_GAINS]:
            schema[vol.Required(CONF_PROPORTIONAL, default=options[CONF_PROPORTIONAL])] = str
            schema[vol.Required(CONF_INTEGRAL, default=options[CONF_INTEGRAL])] = str
            schema[vol.Required(CONF_DERIVATIVE, default=options[CONF_DERIVATIVE])] = str

        if options[CONF_DYNAMIC_MINIMUM_SETPOINT]:
            schema[vol.Required(CONF_MINIMUM_SETPOINT_ADJUSTMENT_FACTOR, default=options[CONF_MINIMUM_SETPOINT_ADJUSTMENT_FACTOR])] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.1, max=0.5, step=0.1)
            )

        if not options[CONF_AUTOMATIC_DUTY_CYCLE]:
            schema[vol.Required(CONF_DUTY_CYCLE, default=options[CONF_DUTY_CYCLE])] = selector.TimeSelector()

        entities = entity_registry.async_get(self.hass)
        device_name = self._config_entry.data.get(CONF_NAME)
        window_id = entities.async_get_entity_id(binary_sensor.DOMAIN, DOMAIN, f"{device_name.lower()}-window-sensor")

        schema[vol.Optional(CONF_WINDOW_SENSORS, default=options[CONF_WINDOW_SENSORS])] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                multiple=True,
                domain=binary_sensor.DOMAIN,
                exclude_entities=[window_id] if window_id else [],
                device_class=[
                    binary_sensor.BinarySensorDeviceClass.DOOR,
                    binary_sensor.BinarySensorDeviceClass.WINDOW,
                    binary_sensor.BinarySensorDeviceClass.GARAGE_DOOR
                ]
            )
        )

        return self.async_show_form(step_id="general", data_schema=vol.Schema(schema))

    async def async_step_presets(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            return await self.update_options(_user_input)

        options = await self.get_options()
        return self.async_show_form(
            step_id="presets",
            data_schema=vol.Schema({
                vol.Required(CONF_ACTIVITY_TEMPERATURE, default=options[CONF_ACTIVITY_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
                ),
                vol.Required(CONF_AWAY_TEMPERATURE, default=options[CONF_AWAY_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
                ),
                vol.Required(CONF_SLEEP_TEMPERATURE, default=options[CONF_SLEEP_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
                ),
                vol.Required(CONF_HOME_TEMPERATURE, default=options[CONF_HOME_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
                ),
                vol.Required(CONF_COMFORT_TEMPERATURE, default=options[CONF_COMFORT_TEMPERATURE]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C")
                ),
                vol.Required(CONF_SYNC_CLIMATES_WITH_PRESET, default=options[CONF_SYNC_CLIMATES_WITH_PRESET]): bool,
                vol.Required(CONF_PUSH_SETPOINT_TO_THERMOSTAT, default=options[CONF_PUSH_SETPOINT_TO_THERMOSTAT]): bool,
            })
        )

    async def async_step_areas(self, user_input: dict[str, Any] | None = None):
        room_weights: dict[str, float] = dict(self._options.get(CONF_ROOM_WEIGHTS, {}))

        room_entity_ids: list[str] = list(self._config_entry.data.get(CONF_ROOMS, []))

        # Build stable schema keys (entity_id) and friendly labels separately
        room_labels: dict[str, str] = {}
        for entity_id in room_entity_ids:
            state = self.hass.states.get(entity_id)
            name = state.name if state else entity_id
            room_labels[entity_id] = f"{name} ({entity_id})"

        if user_input is not None:
            # Persist only currently configured rooms, normalize to float
            new_room_weights: dict[str, float] = {}
            for entity_id in room_entity_ids:
                raw_value = user_input.get(entity_id, room_weights.get(entity_id, 1.0))
                new_room_weights[entity_id] = float(raw_value)

            return await self.update_options({CONF_ROOM_WEIGHTS: new_room_weights})

        schema_fields = {}
        for entity_id in room_entity_ids:
            schema_fields[
                vol.Required(entity_id, default=room_weights.get(entity_id, 1.0))
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.1, max=3.0, step=0.1, mode=selector.NumberSelectorMode.SLIDER)
            )

        return self.async_show_form(
            step_id="areas",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_system_configuration(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            return await self.update_options(_user_input)

        options = await self.get_options()

        schema: dict[Marker, Any] = {
            vol.Required(CONF_AUTOMATIC_DUTY_CYCLE, default=options[CONF_AUTOMATIC_DUTY_CYCLE]): bool,
            vol.Required(CONF_SYNC_CLIMATES_WITH_MODE, default=options[CONF_SYNC_CLIMATES_WITH_MODE]): bool,
        }

        if options.get(CONF_HEATING_SYSTEM) == HEATING_SYSTEM_HEAT_PUMP:
            schema[vol.Required(CONF_CYCLES_PER_HOUR, default=str(options[CONF_CYCLES_PER_HOUR]))] = selector.SelectSelector(
                selector.SelectSelectorConfig(mode=SelectSelectorMode.DROPDOWN, options=[
                    selector.SelectOptionDict(value="2", label="Normal (2x per hour)"),
                    selector.SelectOptionDict(value="3", label="High (3x per hour)"),
                ])
            )

        if options.get(CONF_HEATING_SYSTEM) == HEATING_SYSTEM_RADIATORS:
            schema[vol.Required(CONF_CYCLES_PER_HOUR, default=str(options[CONF_CYCLES_PER_HOUR]))] = selector.SelectSelector(
                selector.SelectSelectorConfig(mode=SelectSelectorMode.DROPDOWN, options=[
                    selector.SelectOptionDict(value="3", label="Normal (3x per hour)"),
                    selector.SelectOptionDict(value="4", label="High (4x per hour)"),
                ])
            )

        schema[vol.Required(CONF_SENSOR_MAX_VALUE_AGE, default=options[CONF_SENSOR_MAX_VALUE_AGE])] = selector.TimeSelector()
        schema[vol.Required(CONF_WINDOW_MINIMUM_OPEN_TIME, default=options[CONF_WINDOW_MINIMUM_OPEN_TIME])] = selector.TimeSelector()

        return self.async_show_form(
            step_id="system_configuration",
            data_schema=vol.Schema(schema)
        )

    async def async_step_advanced(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            return await self.update_options(_user_input)

        options = await self.get_options()

        schema: dict[Marker, Any] = {
            vol.Required(CONF_SIMULATION, default=options[CONF_SIMULATION]): bool,
            vol.Required(CONF_THERMAL_COMFORT, default=options[CONF_THERMAL_COMFORT]): bool,
            vol.Required(CONF_ERROR_MONITORING, default=options[CONF_ERROR_MONITORING]): bool,
            vol.Required(CONF_DYNAMIC_MINIMUM_SETPOINT, default=options[CONF_DYNAMIC_MINIMUM_SETPOINT]): bool,
        }

        if self._config_entry.data.get(CONF_MODE) in [MODE_MQTT_OPENTHERM, MODE_SERIAL, MODE_SIMULATOR]:
            schema[vol.Required(CONF_FORCE_PULSE_WIDTH_MODULATION, default=options[CONF_FORCE_PULSE_WIDTH_MODULATION])] = bool

            schema[vol.Required(CONF_MINIMUM_CONSUMPTION, default=options[CONF_MINIMUM_CONSUMPTION])] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=8, step=0.1)
            )

            schema[vol.Required(CONF_MAXIMUM_CONSUMPTION, default=options[CONF_MAXIMUM_CONSUMPTION])] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=8, step=0.1)
            )

        schema[vol.Required(CONF_CLIMATE_VALVE_OFFSET, default=options[CONF_CLIMATE_VALVE_OFFSET])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=-1, max=1, step=0.1)
        )

        schema[vol.Required(CONF_TARGET_TEMPERATURE_STEP, default=options[CONF_TARGET_TEMPERATURE_STEP])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.1, max=1, step=0.05)
        )

        schema[vol.Required(CONF_MAXIMUM_RELATIVE_MODULATION, default=options[CONF_MAXIMUM_RELATIVE_MODULATION])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=100, step=1)
        )

        return self.async_show_form(
            step_id="advanced",
            data_schema=vol.Schema(schema)
        )

    async def update_options(self, _user_input):
        self._options.update(_user_input)
        return self.async_create_entry(title=self._config_entry.data[CONF_NAME], data=self._options)

    async def get_options(self):
        options = OPTIONS_DEFAULTS.copy()
        options.update(self._options)

        return options
