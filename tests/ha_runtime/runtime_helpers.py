"""Minimal real HA startup prerequisites for entity publication tests."""
from homeassistant.config_entries import ConfigEntries
from homeassistant.helpers import device_registry as dr, entity_registry as er


async def setup_registries(hass):
    """Initialize empty config entries before registry loading, as HA does.

    HA 2026.11 entity-registry loading awaits the config-entry manager, and
    restore-state entities now consult the loaded registry even without an
    attached registry entry. Use actual initialized services across the matrix.
    """
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
