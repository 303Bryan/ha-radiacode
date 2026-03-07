"""Switch platform for the RadiaCode integration.

Five toggle switches per device:
  • BLE Connection   — connect/disconnect the device (allows mobile app access)
  • Sound            — audible click / alarm on/off
  • Vibration        — haptic feedback on/off
  • Display          — OLED screen on/off
  • Backlight        — display backlight on/off
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    DOMAIN,
    SWITCH_BLE_CONNECTED,
    SWITCH_SOUND_ON,
    SWITCH_VIBRO_ON,
    SWITCH_DISPLAY_ON,
    SWITCH_BACKLIGHT_ON,
)
from .coordinator import RadiaCodeCoordinator
from .radiacode_ble.protocol import VSFR

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class RadiaCodeSwitchDescription(SwitchEntityDescription):
    """Extended description carrying the VSFR register ID for writes."""

    vsfr_id: int = 0


SWITCH_DESCRIPTIONS: tuple[RadiaCodeSwitchDescription, ...] = (
    RadiaCodeSwitchDescription(
        key=SWITCH_SOUND_ON,
        name="Sound",
        icon="mdi:volume-high",
        entity_category=EntityCategory.CONFIG,
        vsfr_id=VSFR.SOUND_ON,
    ),
    RadiaCodeSwitchDescription(
        key=SWITCH_VIBRO_ON,
        name="Vibration",
        icon="mdi:vibrate",
        entity_category=EntityCategory.CONFIG,
        vsfr_id=VSFR.VIBRO_ON,
    ),
    RadiaCodeSwitchDescription(
        key=SWITCH_DISPLAY_ON,
        name="Display",
        icon="mdi:monitor",
        entity_category=EntityCategory.CONFIG,
        vsfr_id=VSFR.DISP_ON,
    ),
    RadiaCodeSwitchDescription(
        key=SWITCH_BACKLIGHT_ON,
        name="Backlight",
        icon="mdi:brightness-6",
        entity_category=EntityCategory.CONFIG,
        vsfr_id=VSFR.DISP_BACKLT_ON,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RadiaCode switches from a config entry."""
    coordinator: RadiaCodeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list = [RadiaCodeConnectionSwitch(coordinator, entry)]
    entities.extend(
        RadiaCodeSwitch(coordinator, entry, desc)
        for desc in SWITCH_DESCRIPTIONS
    )
    async_add_entities(entities)


class RadiaCodeSwitch(CoordinatorEntity[RadiaCodeCoordinator], SwitchEntity):
    """A single RadiaCode switch entity backed by a VSFR register."""

    entity_description: RadiaCodeSwitchDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RadiaCodeCoordinator,
        entry: ConfigEntry,
        description: RadiaCodeSwitchDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data[CONF_ADDRESS]}-{description.key}"

        device_name = entry.data.get(CONF_NAME, entry.data[CONF_ADDRESS])
        model = device_name if device_name.startswith("RC-") else "RadiaCode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_ADDRESS])},
            name=device_name,
            manufacturer="Radiacode",
            model=model,
        )

    @property
    def is_on(self) -> Optional[bool]:
        """Return True if the switch is on."""
        if self.coordinator.data is None:
            return None
        return getattr(
            self.coordinator.data.settings,
            self.entity_description.key,
            None,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.coordinator.async_write_setting(
            self.entity_description.vsfr_id, 1
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.coordinator.async_write_setting(
            self.entity_description.vsfr_id, 0
        )


class RadiaCodeConnectionSwitch(CoordinatorEntity[RadiaCodeCoordinator], SwitchEntity):
    """Switch that controls the BLE connection to the RadiaCode device.

    Turn OFF to release the BLE connection so the RadiaCode mobile app (or
    another client) can connect to the device.  Turn ON to re-establish the
    connection and resume sensor polling.

    Unlike the other RadiaCode switches, this entity is not backed by a
    device VSFR register — it controls the integration's own connection state.
    """

    _attr_has_entity_name = True
    _attr_name = "BLE Connection"
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(
        self,
        coordinator: RadiaCodeCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_ADDRESS]}-{SWITCH_BLE_CONNECTED}"

        device_name = entry.data.get(CONF_NAME, entry.data[CONF_ADDRESS])
        model = device_name if device_name.startswith("RC-") else "RadiaCode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_ADDRESS])},
            name=device_name,
            manufacturer="Radiacode",
            model=model,
        )

    @property
    def available(self) -> bool:
        """Always available — the user can always toggle the connection."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True when the BLE connection is active (not user-disconnected)."""
        return not self.coordinator.user_disconnected

    @property
    def icon(self) -> str:
        """Use a different icon when disconnected."""
        return "mdi:bluetooth-connect" if self.is_on else "mdi:bluetooth-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Re-establish the BLE connection and resume polling."""
        await self.coordinator.async_user_reconnect()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disconnect BLE and suspend polling so the mobile app can connect."""
        await self.coordinator.async_user_disconnect()
