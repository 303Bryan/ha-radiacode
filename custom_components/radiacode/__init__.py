"""RadiaCode Home Assistant integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_ADDRESS,
    DOMAIN,
    REMOVED_SENSOR_KEYS,
    SENSOR_RADIATION_ALARM,
)
from .coordinator import RadiaCodeCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up RadiaCode from a config entry.

    Does NOT block startup with async_config_entry_first_refresh().
    BLE connections through ESPHome proxies can take many attempts (each
    15-30 s); blocking here stalls the entire HA boot for minutes.

    Instead, entities are created immediately (starting as unavailable)
    and the coordinator begins polling once the first entity subscribes.
    The BLE connection is established on the first successful poll cycle.
    """
    coordinator = RadiaCodeCoordinator(hass, entry)

    # Clean up registry entries for entities removed by past migrations:
    #  • 1.1.0: Radiation Alarm moved from binary_sensor to an enum sensor
    #  • 1.3.0: accelerometer sensors removed (registers rejected over BLE)
    ent_reg = er.async_get(hass)
    address = entry.data[CONF_ADDRESS]
    stale_ids = [
        (Platform.BINARY_SENSOR, f"{address}-{SENSOR_RADIATION_ALARM}"),
    ] + [(Platform.SENSOR, f"{address}-{key}") for key in REMOVED_SENSOR_KEYS]
    for platform, unique_id in stale_ids:
        stale = ent_reg.async_get_entity_id(platform, DOMAIN, unique_id)
        if stale is not None:
            ent_reg.async_remove(stale)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry when options (e.g. poll interval) change.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Release the BLE connection on HA shutdown so the device is free for
    # other clients (mobile app) while HA is down.
    async def _async_on_ha_stop(_event: Event) -> None:
        await coordinator.async_shutdown()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_on_ha_stop)
    )

    # Polling starts automatically when the first entity subscribes to
    # coordinator updates (via CoordinatorEntity.async_added_to_hass).
    # The first poll runs after update_interval, which is when the BLE
    # connection will be established.

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a RadiaCode config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: RadiaCodeCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
