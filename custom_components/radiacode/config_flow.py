"""Config flow for the RadiaCode integration.

Supports two entry paths:
  1. Automatic Bluetooth discovery — HA detects an "RC-*" device and presents
     a one-click confirmation dialog.
  2. Manual entry — user supplies the Bluetooth address directly (useful when
     the device is connected via an ESPHome BT proxy that hides local scanning).
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_ADDRESS, CONF_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

_MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
        vol.Optional(CONF_NAME, default=""): str,
    }
)


class RadiaCodeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for RadiaCode."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._name: str = ""

    # ── Bluetooth auto-discovery path ─────────────────────────────────────────

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle a device discovered via HA's Bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        self._name = discovery_info.name or discovery_info.address

        _LOGGER.debug(
            "Discovered RadiaCode device: %s (%s)",
            self._name,
            discovery_info.address,
        )
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask the user to confirm adding the discovered device."""
        if self._discovery_info is None:
            # Reached confirm step without going through bluetooth discovery;
            # fall back to manual entry.
            return await self.async_step_user()

        if user_input is not None:
            # Re-check unique_id right before creating the entry.  A race
            # during HA boot can allow the confirm flow to proceed even
            # though the entry was already configured by a parallel flow.
            await self.async_set_unique_id(self._discovery_info.address)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_ADDRESS: self._discovery_info.address,
                    CONF_NAME: self._name,
                },
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name},
        )

    # ── Manual entry path ─────────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual configuration (no auto-discovery)."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS].upper().strip()
            name = user_input.get(CONF_NAME, "").strip() or address

            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data={CONF_ADDRESS: address, CONF_NAME: name},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_MANUAL_SCHEMA,
        )
