import logging
import traceback

from homeassistant.components import binary_sensor, climate, number, sensor
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.storage import Store
from sentry_sdk import Client, Hub

from .const import DOMAIN, OPTIONS_DEFAULTS
from .coordinator import SatDataUpdateCoordinatorFactory
from .entry_data import SatConfig, SatEntryData
from .services import async_register_services

_LOGGER: logging.Logger = logging.getLogger(__name__)
PLATFORMS = [climate.DOMAIN, sensor.DOMAIN, number.DOMAIN, binary_sensor.DOMAIN]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    hass.data.setdefault(DOMAIN, {})

    config = SatConfig(entry.entry_id, entry.data, {**OPTIONS_DEFAULTS, **entry.options})
    await async_migrate_identifiers(hass, entry, config)

    coordinator = SatDataUpdateCoordinatorFactory().resolve(hass=hass, config=config)
    hass.data[DOMAIN][entry.entry_id] = entry_data = SatEntryData(coordinator=coordinator, config=config)

    try:
        if config.error_monitoring_enabled:
            def create_sentry_client() -> None:
                entry_data.sentry = initialize_sentry()

            await hass.async_add_executor_job(create_sentry_client)
    except Exception as error:
        _LOGGER.error("Error during Sentry initialization: %s", error)

    await coordinator.async_setup()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if entry_data is None:
        return True

    try:
        unload_successful = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    except ValueError:
        _LOGGER.debug("Platforms already unloaded for entry %s.", entry.entry_id)
        unload_successful = True

    if not unload_successful:
        return False

    try:
        if entry_data.sentry is not None:
            entry_data.sentry.flush()
            entry_data.sentry.close()
            entry_data.sentry = None
    except Exception as error:
        _LOGGER.error("Error during Sentry cleanup: %s", error)

    hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN, None)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry."""
    # Unload the entry and its dependent components
    if not await async_unload_entry(hass, entry):
        _LOGGER.warning("Reload skipped: unload failed for entry %s.", entry.entry_id)
        return

    # Set up the entry again
    await async_setup_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    from .config_flow import SatFlowHandler
    _LOGGER.debug("Migrating from version %s", entry.version)

    if entry.version < SatFlowHandler.VERSION:
        new_data = {**entry.data}
        new_options = {**entry.options}

        if entry.version < 2:
            if not entry.data.get("minimum_setpoint"):
                # Legacy Store
                store = Store(hass, 1, DOMAIN)
                new_data["minimum_setpoint"] = 10

                if (data := await store.async_load()) and (overshoot_protection_value := data.get("overshoot_protection_value")):
                    new_data["minimum_setpoint"] = overshoot_protection_value

            if entry.options.get("heating_system") == "underfloor":
                new_data["heating_system"] = "underfloor"
            else:
                new_data["heating_system"] = "radiators"

            if not entry.data.get("maximum_setpoint"):
                new_data["maximum_setpoint"] = 55

                if entry.options.get("heating_system") == "underfloor":
                    new_data["maximum_setpoint"] = 50

                if entry.options.get("heating_system") == "radiator_low_temperatures":
                    new_data["maximum_setpoint"] = 55

                if entry.options.get("heating_system") == "radiator_medium_temperatures":
                    new_data["maximum_setpoint"] = 65

                if entry.options.get("heating_system") == "radiator_high_temperatures":
                    new_data["maximum_setpoint"] = 75

        if entry.version < 3:
            if main_climates := entry.options.get("main_climates"):
                new_data["main_climates"] = main_climates
                new_options.pop("main_climates")

            if secondary_climates := entry.options.get("climates"):
                new_data["secondary_climates"] = secondary_climates
                new_options.pop("climates")

            if sync_with_thermostat := entry.options.get("sync_with_thermostat"):
                new_data["sync_with_thermostat"] = sync_with_thermostat
                new_options.pop("sync_with_thermostat")

        if entry.version < 4:
            if entry.data.get("window_sensor") is not None:
                new_data["window_sensors"] = [entry.data.get("window_sensor")]
                del new_options["window_sensor"]

        if entry.version < 5:
            if entry.options.get("overshoot_protection") is not None:
                new_data["overshoot_protection"] = entry.options.get("overshoot_protection")
                del new_options["overshoot_protection"]

        if entry.version < 7:
            new_options["pid_controller_version"] = 1

        if entry.version < 8:
            if entry.options.get("heating_curve_version") is not None and int(entry.options.get("heating_curve_version")) < 2:
                new_options["heating_curve_version"] = 3

        if entry.version < 9:
            if entry.data.get("heating_system") == "heat_pump":
                new_options["cycles_per_hour"] = 2

            if entry.data.get("heating_system") == "radiators":
                new_options["cycles_per_hour"] = 3

        if entry.version < 10:
            if entry.data.get("mode") == "mqtt":
                device = device_registry.async_get(hass).async_get(entry.data.get("device"))

                new_data["mode"] = "mqtt_opentherm"
                new_data["device"] = list(device.identifiers)[0][1]

        if entry.version < 11:
            if entry.data.get("sync_with_thermostat") is not None:
                new_data["push_setpoint_to_thermostat"] = entry.data.get("sync_with_thermostat")

        hass.config_entries.async_update_entry(entry, version=SatFlowHandler.VERSION, data=new_data, options=new_options)

    _LOGGER.info("Migration to version %s successful", entry.version)

    return True


async def async_migrate_identifiers(hass: HomeAssistant, entry: ConfigEntry, config: SatConfig) -> None:
    entity_reg = entity_registry.async_get(hass)
    device_reg = device_registry.async_get(hass)

    old_prefix = config.name_lower
    new_prefix = config.entry_id
    old_dash_prefix = f"{old_prefix}-"

    for entity_entry in entity_registry.async_entries_for_config_entry(entity_reg, entry.entry_id):
        unique_id = entity_entry.unique_id
        new_unique_id = None

        if unique_id == old_prefix:
            new_unique_id = new_prefix
        elif unique_id.startswith(old_dash_prefix):
            new_unique_id = f"{new_prefix}-{unique_id[len(old_dash_prefix):]}"

        if new_unique_id and new_unique_id != unique_id:
            entity_reg.async_update_entity(entity_entry.entity_id, new_unique_id=new_unique_id)

    device = device_reg.async_get_device(identifiers={(DOMAIN, config.name)})
    if device is None:
        return

    device_reg.async_update_device(device.id, new_identifiers={(DOMAIN, config.entry_id)})


def initialize_sentry() -> Client:
    """Initialize Sentry synchronously in an offloaded executor job."""

    def exception_filter(event, hint):
        """Filter events to send only SAT-related exceptions to Sentry."""
        exc_info = hint.get("exc_info")

        if exc_info:
            _, _, exc_traceback = exc_info
            stack = traceback.extract_tb(exc_traceback)

            # Check if the exception originates from the SAT custom component
            if any("custom_components/sat/" in frame.filename for frame in stack):
                return event

        # Ignore exceptions not related to SAT
        return None

    # Configure the Sentry client
    client = Client(
        traces_sample_rate=1.0,
        before_send=exception_filter,
        dsn="https://144b75a0111295466e4f7f438ee79bbe@o4508432869621760.ingest.de.sentry.io/4508432872898640",
    )

    # Bind the Sentry client to the Sentry hub
    hub = Hub(client)
    hub.bind_client(client)

    return client
