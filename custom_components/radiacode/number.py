"""Number platform for the RadiaCode integration.

Seven numeric sliders per device:
  • Display Brightness       — 0-9
  • Dose Rate Alarm L1/L2    — µSv/h
  • Accumulated Dose Alarm L1/L2 — µSv
  • Count Rate Alarm L1/L2   — CPS

Alarm thresholds are converted between device-native units and HA-facing
units via from_device / to_device callables on the description.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    DOMAIN,
    NUMBER_DISPLAY_BRIGHTNESS,
    NUMBER_DR_ALARM_L1,
    NUMBER_DR_ALARM_L2,
    NUMBER_DS_ALARM_L1,
    NUMBER_DS_ALARM_L2,
    NUMBER_CR_ALARM_L1,
    NUMBER_CR_ALARM_L2,
    build_device_info,
)
from .coordinator import RadiaCodeCoordinator
from .radiacode_ble.protocol import VSFR

_LOGGER = logging.getLogger(__name__)


# ── Unit conversion helpers ──────────────────────────────────────────────────
# Device stores µR; HA shows µSv → ÷100 on read, ×100 on write.
# Device stores counts/10s; HA shows CPS → ÷10 on read, ×10 on write.

def _identity(v: float) -> float:
    return v

def _uR_to_uSv(v: float) -> float:
    return v / 100.0

def _uSv_to_uR(v: float) -> float:
    return v * 100.0

def _cp10s_to_cps(v: float) -> float:
    return v / 10.0

def _cps_to_cp10s(v: float) -> float:
    return v * 10.0


@dataclass(frozen=True, kw_only=True)
class RadiaCodeNumberDescription(NumberEntityDescription):
    """Extended description with VSFR ID and unit conversion callables."""

    vsfr_id: int = 0
    from_device: Callable[[float], float] = _identity
    to_device: Callable[[float], float] = _identity


NUMBER_DESCRIPTIONS: tuple[RadiaCodeNumberDescription, ...] = (
    RadiaCodeNumberDescription(
        key=NUMBER_DISPLAY_BRIGHTNESS,
        name="Display Brightness",
        icon="mdi:brightness-6",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=9,
        native_step=1,
        mode=NumberMode.SLIDER,
        vsfr_id=VSFR.DISP_BRT,
    ),
    RadiaCodeNumberDescription(
        key=NUMBER_DR_ALARM_L1,
        name="Dose Rate Alarm L1",
        icon="mdi:alert",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement="µSv/h",
        native_min_value=0,
        native_max_value=10000,
        native_step=0.01,
        mode=NumberMode.BOX,
        vsfr_id=VSFR.DR_LEV1_uR_h,
        from_device=_uR_to_uSv,
        to_device=_uSv_to_uR,
    ),
    RadiaCodeNumberDescription(
        key=NUMBER_DR_ALARM_L2,
        name="Dose Rate Alarm L2",
        icon="mdi:alert",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement="µSv/h",
        native_min_value=0,
        native_max_value=10000,
        native_step=0.01,
        mode=NumberMode.BOX,
        vsfr_id=VSFR.DR_LEV2_uR_h,
        from_device=_uR_to_uSv,
        to_device=_uSv_to_uR,
    ),
    RadiaCodeNumberDescription(
        key=NUMBER_DS_ALARM_L1,
        name="Dose Alarm L1",
        icon="mdi:alert-circle",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement="µSv",
        native_min_value=0,
        native_max_value=1000000,
        native_step=0.01,
        mode=NumberMode.BOX,
        vsfr_id=VSFR.DS_LEV1_uR,
        from_device=_uR_to_uSv,
        to_device=_uSv_to_uR,
    ),
    RadiaCodeNumberDescription(
        key=NUMBER_DS_ALARM_L2,
        name="Dose Alarm L2",
        icon="mdi:alert-circle",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement="µSv",
        native_min_value=0,
        native_max_value=1000000,
        native_step=0.01,
        mode=NumberMode.BOX,
        vsfr_id=VSFR.DS_LEV2_uR,
        from_device=_uR_to_uSv,
        to_device=_uSv_to_uR,
    ),
    RadiaCodeNumberDescription(
        key=NUMBER_CR_ALARM_L1,
        name="Count Rate Alarm L1",
        icon="mdi:pulse",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement="CPS",
        native_min_value=0,
        native_max_value=100000,
        native_step=0.1,
        mode=NumberMode.BOX,
        vsfr_id=VSFR.CR_LEV1_cp10s,
        from_device=_cp10s_to_cps,
        to_device=_cps_to_cp10s,
    ),
    RadiaCodeNumberDescription(
        key=NUMBER_CR_ALARM_L2,
        name="Count Rate Alarm L2",
        icon="mdi:pulse",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement="CPS",
        native_min_value=0,
        native_max_value=100000,
        native_step=0.1,
        mode=NumberMode.BOX,
        vsfr_id=VSFR.CR_LEV2_cp10s,
        from_device=_cp10s_to_cps,
        to_device=_cps_to_cp10s,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RadiaCode number entities from a config entry."""
    coordinator: RadiaCodeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RadiaCodeNumber(coordinator, entry, desc)
        for desc in NUMBER_DESCRIPTIONS
    )


class RadiaCodeNumber(CoordinatorEntity[RadiaCodeCoordinator], NumberEntity):
    """A single RadiaCode numeric setting entity backed by a VSFR register."""

    entity_description: RadiaCodeNumberDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RadiaCodeCoordinator,
        entry: ConfigEntry,
        description: RadiaCodeNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data[CONF_ADDRESS]}-{description.key}"
        self._attr_device_info = build_device_info(
            entry.data[CONF_ADDRESS],
            entry.data.get(CONF_NAME, entry.data[CONF_ADDRESS]),
        )

    @property
    def native_value(self) -> Optional[float]:
        """Return the current value converted to HA units."""
        if self.coordinator.data is None:
            return None
        raw = getattr(
            self.coordinator.data.settings,
            self.entity_description.key,
            None,
        )
        if raw is None:
            return None
        return self.entity_description.from_device(float(raw))

    async def async_set_native_value(self, value: float) -> None:
        """Write the new value to the device in device-native units."""
        device_value = int(round(self.entity_description.to_device(value)))
        await self.coordinator.async_write_setting(
            self.entity_description.vsfr_id, device_value
        )
