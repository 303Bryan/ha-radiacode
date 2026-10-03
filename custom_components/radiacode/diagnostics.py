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

TO_REDACT = {
    CONF_ADDRESS,
    CONF_NAME,
    "source",
    "serial_number",
    "advertisement_source_at_connect",
    "connection_request_source",
    "connection_request_source_name",
    "connection_request_scanner_source",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: RadiaCodeCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data
    sensors = asdict(data.sensors) if data is not None else None
    if sensors is not None and sensors.get("measurement_time") is not None:
        sensors["measurement_time"] = sensors["measurement_time"].isoformat()

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "device": {
            "serial_number": "**REDACTED**" if coordinator.serial_number else None,
            "firmware_version": coordinator.firmware_version,
            # The device's self-describing SFR register directory: every
            # register the firmware supports, with address, size, type,
            # and signedness.  None if not yet read or if the firmware
            # returns it empty over BLE (observed on RC-103 FW 4.14).
            "sfr_register_directory": (
                coordinator.sfr_file.splitlines()
                if coordinator.sfr_file
                else None
            ),
        },
        "connection": {
            "ble_connected": coordinator.is_ble_connected,
            "user_disconnected": coordinator.user_disconnected,
            "connection_count": coordinator.connection_count,
            "last_error": coordinator.last_error,
            "last_poll_duration": coordinator.last_poll_duration,
            "last_update_success": coordinator.last_update_success,
        },
        "sensors": sensors,
        "settings": asdict(data.settings) if data is not None else None,
        "device_health": asdict(data.diagnostics) if data is not None else None,
        "spectrum": coordinator.spectrum_status,
        "runtime": async_redact_data(coordinator.runtime_status, TO_REDACT),
        "transport": async_redact_data(
            coordinator.transport_status, TO_REDACT
        ),
    }
