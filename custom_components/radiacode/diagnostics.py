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
from .radiacode_ble.firmware_profiles import PROFILES

TO_REDACT = {
    CONF_ADDRESS,
    CONF_NAME,
    "source",
    "serial_number",
    "advertisement_source_at_connect",
    "connection_request_source",
    "connection_request_source_name",
    "connection_request_scanner_source",
    "active_connection_source",
    "active_connection_source_name",
}

_CONFIGURATION_SOURCES = {"device", "storage", "firmware_profile"}
_PUBLIC_PROFILE_NAMES = {profile.name for profile in PROFILES}


def _redact_transport(status: dict[str, Any]) -> dict[str, Any]:
    """Keep known protocol provenance labels while redacting device identities."""
    redacted = async_redact_data(status, TO_REDACT)
    configuration = status.get("configuration")
    if isinstance(configuration, dict):
        # The generic source/name keys also describe Bluetooth identities.
        # Restore only allowlisted values at these precise protocol paths.
        source = configuration.get("source")
        if isinstance(source, str) and source in _CONFIGURATION_SOURCES:
            redacted["configuration"]["source"] = source
        profile = configuration.get("firmware_profile")
        if source == "firmware_profile" and isinstance(profile, dict):
            name = profile.get("name")
            if isinstance(name, str) and name in _PUBLIC_PROFILE_NAMES:
                redacted["configuration"]["firmware_profile"]["name"] = name
    return redacted


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: RadiaCodeCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data
    sensors = asdict(data.sensors) if data is not None else None
    if sensors is not None:
        for key in ("measurement_time", "temperature_time", "accumulated_dose_time"):
            if sensors.get(key) is not None:
                sensors[key] = sensors[key].isoformat()

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
        "transport": _redact_transport(coordinator.transport_status),
    }
