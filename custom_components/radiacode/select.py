"""Select platform for the RadiaCode integration.

Two dropdown selects per device:
  • Display Direction   — Auto / Right / Left
  • Display Auto-Off    — 5s / 10s / 15s / 30s
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    DOMAIN,
    SELECT_DISPLAY_DIRECTION,
    SELECT_DISPLAY_OFF_TIME,
    build_device_info,
)
from .coordinator import RadiaCodeCoordinator
from .radiacode_ble.protocol import VSFR

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class RadiaCodeSelectDescription(SelectEntityDescription):
    """Extended description with VSFR ID and option↔device-value mappings."""

    vsfr_id: int = 0
    # Maps HA option label → device register value
    option_to_device: dict[str, int] = field(default_factory=dict)
    # Maps device register value → HA option label
    device_to_option: dict[int, str] = field(default_factory=dict)


SELECT_DESCRIPTIONS: tuple[RadiaCodeSelectDescription, ...] = (
    RadiaCodeSelectDescription(
        key=SELECT_DISPLAY_DIRECTION,
        name="Display Direction",
        icon="mdi:rotate-3d-variant",
        entity_category=EntityCategory.CONFIG,
        options=["Auto", "Right", "Left"],
        vsfr_id=VSFR.DISP_DIR,
        option_to_device={"Auto": 0, "Right": 1, "Left": 2},
        device_to_option={0: "Auto", 1: "Right", 2: "Left"},
    ),
    RadiaCodeSelectDescription(
        key=SELECT_DISPLAY_OFF_TIME,
        name="Display Auto-Off",
        icon="mdi:timer-outline",
        entity_category=EntityCategory.CONFIG,
        options=["5s", "10s", "15s", "30s"],
        vsfr_id=VSFR.DISP_OFF_TIME,
        option_to_device={"5s": 0, "10s": 1, "15s": 2, "30s": 3},
        device_to_option={0: "5s", 1: "10s", 2: "15s", 3: "30s"},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RadiaCode select entities from a config entry."""
    coordinator: RadiaCodeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RadiaCodeSelect(coordinator, entry, desc)
        for desc in SELECT_DESCRIPTIONS
    )


class RadiaCodeSelect(CoordinatorEntity[RadiaCodeCoordinator], SelectEntity):
    """A single RadiaCode select entity backed by a VSFR register."""

    entity_description: RadiaCodeSelectDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RadiaCodeCoordinator,
        entry: ConfigEntry,
        description: RadiaCodeSelectDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data[CONF_ADDRESS]}-{description.key}"
        self._attr_device_info = build_device_info(
            entry.data[CONF_ADDRESS],
            entry.data.get(CONF_NAME, entry.data[CONF_ADDRESS]),
        )

    @property
    def current_option(self) -> Optional[str]:
        """Return the currently selected option."""
        if self.coordinator.data is None:
            return None
        raw = getattr(
            self.coordinator.data.settings,
            self.entity_description.key,
            None,
        )
        if raw is None:
            return None
        return self.entity_description.device_to_option.get(int(raw))

    async def async_select_option(self, option: str) -> None:
        """Write the selected option to the device."""
        device_value = self.entity_description.option_to_device[option]
        await self.coordinator.async_write_setting(
            self.entity_description.vsfr_id, device_value
        )
