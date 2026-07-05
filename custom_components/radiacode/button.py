"""Button platform for the RadiaCode integration.

Two buttons per device:
  • Dose Reset     — resets the accumulated dose counter to zero
  • Spectrum Reset — clears the current gamma spectrum accumulation
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    DOMAIN,
    BUTTON_DOSE_RESET,
    BUTTON_SPECTRUM_RESET,
    build_device_info,
)
from .coordinator import RadiaCodeCoordinator

_LOGGER = logging.getLogger(__name__)


BUTTON_DESCRIPTIONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(
        key=BUTTON_DOSE_RESET,
        name="Dose Reset",
        icon="mdi:restart",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key=BUTTON_SPECTRUM_RESET,
        name="Spectrum Reset",
        icon="mdi:chart-histogram",
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
    """A RadiaCode action button (dose reset / spectrum reset)."""

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
        self._attr_device_info = build_device_info(
            entry.data[CONF_ADDRESS],
            entry.data.get(CONF_NAME, entry.data[CONF_ADDRESS]),
        )

    async def async_press(self) -> None:
        """Execute the reset action for this button."""
        if self.entity_description.key == BUTTON_SPECTRUM_RESET:
            await self.coordinator.async_reset_spectrum()
        else:
            await self.coordinator.async_reset_dose()
