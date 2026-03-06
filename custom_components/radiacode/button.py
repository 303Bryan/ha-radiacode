"""Button platform for the RadiaCode integration.

One button per device:
  • Dose Reset — resets the accumulated dose counter to zero
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
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
    BUTTON_DOSE_RESET,
)
from .coordinator import RadiaCodeCoordinator
from .radiacode_ble.protocol import VSFR

_LOGGER = logging.getLogger(__name__)


BUTTON_DESCRIPTIONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(
        key=BUTTON_DOSE_RESET,
        name="Dose Reset",
        icon="mdi:restart",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RadiaCode buttons from a config entry."""
    coordinator: RadiaCodeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RadiaCodeButton(coordinator, entry, desc)
        for desc in BUTTON_DESCRIPTIONS
    )


class RadiaCodeButton(CoordinatorEntity[RadiaCodeCoordinator], ButtonEntity):
    """A button to reset the accumulated dose counter."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RadiaCodeCoordinator,
        entry: ConfigEntry,
        description: ButtonEntityDescription,
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

    async def async_press(self) -> None:
        """Reset accumulated dose by writing 1 to DOSE_RESET register."""
        await self.coordinator.async_write_setting(VSFR.DOSE_RESET, 1)
