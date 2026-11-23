import logging
import traceback

from homeassistant.components.climate import PRESET_HOME
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
from homeassistant.helpers.storage import Store
from sentry_sdk import Client, get_global_scope

from .const import (
    CONF_FLOW_SETPOINT_OFFSET_CELSIUS,
    CONF_MODULATION_SUPPRESSION_OFFSET_CELSIUS,
    CONF_ROOM_PRESET_FIXED,
    CONF_ROOM_PRESET_TEMPERATURES,
    CONF_ROOM_PRESETS,
    CONF_ROOMS,
    DOMAIN,
    OPTIONS_DEFAULTS,
)
from .coordinator import SatDataUpdateCoordinatorFactory
from .demand_control import SatDemandControl
from .entry_data import SatConfig, SatEntryData
from .entry_stores import EntryStores
from .heating_control import SatHeatingControl

_LOGGER: logging.Logger = logging.getLogger(__name__)
PLATFORMS = ["climate", "sensor", "number", "binary_sensor", "water_heater"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    hass.data.setdefault(DOMAIN, {})

    config = SatConfig(entry.entry_id, entry.data, {**OPTIONS_DEFAULTS, **entry.options})
    await async_migrate_identifiers(hass, entry, config)

    coordinator = SatDataUpdateCoordinatorFactory().resolve(hass=hass, config=config)
    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=config)
    demand_control = SatDemandControl(hass=hass, coordinator=coordinator, heating_control=heating_control, config=config)

    hass.data[DOMAIN][entry.entry_id] = entry_data = SatEntryData(
        config=config,
        coordinator=coordinator,
        heating_control=heating_control,
        demand_control=demand_control,
    )

    try:
        if config.error_monitoring_enabled:
            def create_sentry_client() -> None:
                entry_data.sentry = initialize_sentry()

            await hass.async_add_executor_job(create_sentry_client)
    except Exception as error:
        _LOGGER.error("Error during Sentry initialization: %s", error)

    await coordinator.async_setup()
    await coordinator.async_added_to_hass(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    entry.async_on_unload(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, entry_data.async_stop))

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

    try:
        await entry_data.demand_control.async_will_remove_from_hass()
    except Exception as error:
        _LOGGER.error("Error during demand control cleanup: %s", error)

    try:
        if getattr(entry_data, "coordinator", None) is not None:
            await entry_data.coordinator.async_release()
    except Exception as error:
        _LOGGER.error("Error while releasing the boiler: %s", error)

    try:
        if getattr(entry_data, "coordinator", None) is not None:
            await entry_data.coordinator.async_will_remove_from_hass()
    except Exception as error:
        _LOGGER.error("Error during coordinator cleanup: %s", error)

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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove what was stored for a config entry."""
    await EntryStores(hass, entry).async_remove()


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


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
            if (window_sensor := entry.options.get("window_sensor") or entry.data.get("window_sensor")) is not None:
                new_options["window_sensors"] = [window_sensor]

            new_data.pop("window_sensor", None)
            new_options.pop("window_sensor", None)

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
                new_data["mode"] = "mqtt_opentherm"

                device = device_registry.async_get(hass).async_get(entry.data.get("device"))
                if device is not None and device.identifiers:
                    new_data["device"] = list(device.identifiers)[0][1]
                else:
                    _LOGGER.warning("Device %s no longer exists, keeping the configured value.", entry.data.get("device"))

        if entry.version < 11:
            if entry.data.get("sync_with_thermostat") is not None:
                new_data["push_setpoint_to_thermostat"] = entry.data.get("sync_with_thermostat")

            if entry.data.get("mode") == "esphome":
                device = device_registry.async_get(hass).async_get(entry.data.get("device"))

                for entry_id in (device.config_entries if device is not None else []):
                    config_entry = hass.config_entries.async_get_entry(entry_id)
                    if config_entry and config_entry.domain == "esphome":
                        new_data["device"] = entry_id
                        break

        if entry.version < 12:
            if (push_setpoint := new_data.pop("push_setpoint_to_thermostat", None)) is not None:
                new_options["push_setpoint_to_thermostat"] = push_setpoint

        if entry.version < 13:
            new_options[CONF_ROOM_PRESETS] = async_migrate_room_presets(hass, entry, new_data.get(CONF_ROOMS) or [], new_options.get(CONF_ROOM_PRESETS) or {})

        hass.config_entries.async_update_entry(entry, version=SatFlowHandler.VERSION, data=new_data, options=new_options)

    _LOGGER.info("Migration to version %s successful", entry.version)

    return True


def async_migrate_room_presets(hass: HomeAssistant, entry: ConfigEntry, rooms: list[str], room_presets: dict) -> dict:
    """Turn the home temperatures the climate entity remembered per room into room presets."""
    remembered = {}
    registry = entity_registry.async_get(hass)
    climate_id = registry.async_get_entity_id("climate", DOMAIN, entry.entry_id)
    restore_state = hass.data.get(DATA_RESTORE_STATE)

    if climate_id is not None and restore_state is not None:
        registry_entry = registry.async_get(climate_id)
        stored = (
                (getattr(restore_state, "last_states", None) or {}).get(climate_id)
                or (getattr(restore_state, "last_states_by_entity_id", None) or {}).get(climate_id)
                or ((getattr(restore_state, "last_states_by_entity_registry_id", None) or {}).get(registry_entry.id) if registry_entry is not None else None)
        )

        if stored is not None:
            remembered = stored.state.attributes.get("rooms") or {}

    migrated = {room: dict(settings or {}) for room, settings in room_presets.items()}

    for room in rooms:
        if (home_target := remembered.get(room)) is None:
            continue

        settings = migrated.setdefault(room, {})
        temperatures = dict(settings.get(CONF_ROOM_PRESET_TEMPERATURES) or {})
        temperatures.setdefault(PRESET_HOME, float(home_target))

        settings[CONF_ROOM_PRESET_FIXED] = bool(settings.get(CONF_ROOM_PRESET_FIXED))
        settings[CONF_ROOM_PRESET_TEMPERATURES] = temperatures

    return migrated


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

    devices = device_registry.async_entries_for_config_entry(device_reg, entry.entry_id)
    if (device := next((device for device in devices if (DOMAIN, config.name) in device.identifiers), None)) is None:
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

    # Bind the Sentry client to the global scope
    get_global_scope().set_client(client)

    return client
