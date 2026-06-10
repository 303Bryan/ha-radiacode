"""Diagnostics support for the RadiaCode integration.

Provides a downloadable diagnostics dump from the device page
(Settings → Devices & Services → Radiacode → Download diagnostics).
The Bluetooth address and device name are redacted because the name
embeds the device serial number.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, CONF_NAME, DOMAIN
from .coordinator import RadiaCodeCoordinator

TO_REDACT = {CONF_ADDRESS, CONF_NAME}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: RadiaCodeCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "device": {
            "serial_number": "**REDACTED**" if coordinator.serial_number else None,
            "firmware_version": coordinator.firmware_version,
        },
        "connection": {
            "ble_connected": coordinator.is_ble_connected,
            "user_disconnected": coordinator.user_disconnected,
            "connection_count": coordinator.connection_count,
            "last_error": coordinator.last_error,
            "last_poll_duration": coordinator.last_poll_duration,
            "last_update_success": coordinator.last_update_success,
        },
        "sensors": asdict(data.sensors) if data is not None else None,
        "settings": asdict(data.settings) if data is not None else None,
    }
