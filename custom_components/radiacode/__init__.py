"""RadiaCode Home Assistant integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Polling starts automatically when the first entity subscribes to
    # coordinator updates (via CoordinatorEntity.async_added_to_hass).
    # The first poll runs after update_interval (5 s), which is when
    # the BLE connection will be established.

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a RadiaCode config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: RadiaCodeCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator._client.disconnect()
    return unload_ok
