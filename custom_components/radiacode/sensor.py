"""Sensor platform for the RadiaCode integration.

Four sensors per device:
  • Dose Rate          µSv/h   — real-time ambient radiation dose rate
  • Count Rate         cps     — raw detector counts per second
  • Accumulated Dose   µSv     — total dose since last reset
  • Battery            %       — device battery level (diagnostic)
"""

from __future__ import annotations

import logging
from typing import Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    DOMAIN,
    SENSOR_ACCUMULATED_DOSE,
    SENSOR_BATTERY,
    SENSOR_COUNT_RATE,
    SENSOR_DOSE_RATE,
)
from .coordinator import RadiaCodeCoordinator
from .radiacode_ble.protocol import RadiaCodeData

_LOGGER = logging.getLogger(__name__)

SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key=SENSOR_DOSE_RATE,
        name="Dose Rate",
        native_unit_of_measurement="µSv/h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        icon="mdi:radioactive",
    ),
    SensorEntityDescription(
        key=SENSOR_COUNT_RATE,
        name="Count Rate",
        native_unit_of_measurement="cps",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:pulse",
    ),
    SensorEntityDescription(
        key=SENSOR_ACCUMULATED_DOSE,
        name="Accumulated Dose",
        native_unit_of_measurement="µSv",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=4,
        icon="mdi:radioactive",
    ),
    SensorEntityDescription(
        key=SENSOR_BATTERY,
        name="Battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RadiaCode sensors from a config entry."""
    coordinator: RadiaCodeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RadiaCodeSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class RadiaCodeSensor(CoordinatorEntity[RadiaCodeCoordinator], SensorEntity):
    """A single RadiaCode sensor entity backed by the polling coordinator."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RadiaCodeCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data[CONF_ADDRESS]}-{description.key}"

        # Extract the model string from the BT local name (e.g. "RC-103").
        device_name = entry.data.get(CONF_NAME, entry.data[CONF_ADDRESS])
        model = device_name if device_name.startswith("RC-") else "RadiaCode"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_ADDRESS])},
            name=device_name,
            manufacturer="Radiacode",
            model=model,
        )

    @property
    def native_value(self) -> Optional[float]:
        """Return the current sensor value from the coordinator's data."""
        if self.coordinator.data is None:
            return None
        data: RadiaCodeData = self.coordinator.data
        return getattr(data, self.entity_description.key, None)
