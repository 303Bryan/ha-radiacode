"""Sensor platform for the RadiaCode integration.

Sensors per device:
  • Dose Rate          µSv/h   — real-time ambient radiation dose rate
  • Count Rate         cps     — raw detector counts per second
  • Accumulated Dose   µSv     — total dose since last reset
  • Battery            %       — device battery level (diagnostic)
  • Temperature        °C      — device temperature (diagnostic)
  • RSSI               dBm     — BLE signal strength from advertisements (diagnostic)
"""

from __future__ import annotations

import logging
from typing import Optional

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
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
    SENSOR_RSSI,
    SENSOR_TEMPERATURE,
    build_device_info,
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
    SensorEntityDescription(
        key=SENSOR_TEMPERATURE,
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
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
    entities: list = [
        RadiaCodeSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.append(RadiaCodeRSSISensor(coordinator, entry))
    async_add_entities(entities)


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
        self._attr_device_info = build_device_info(
            entry.data[CONF_ADDRESS],
            entry.data.get(CONF_NAME, entry.data[CONF_ADDRESS]),
        )

    @property
    def native_value(self) -> Optional[float]:
        """Return the current sensor value from the coordinator's data."""
        if self.coordinator.data is None:
            return None
        data: RadiaCodeData = self.coordinator.data.sensors
        return getattr(data, self.entity_description.key, None)


class RadiaCodeRSSISensor(CoordinatorEntity[RadiaCodeCoordinator], SensorEntity):
    """BLE signal strength (RSSI) reported by the HA Bluetooth scanner.

    RSSI is sourced from BLE advertisement packets, which the device broadcasts
    continuously regardless of whether there is an active connection.  This
    sensor therefore remains available even when the BLE connection is off.

    In addition to the 5-second coordinator poll, this entity subscribes to
    BLE advertisement callbacks so the RSSI value refreshes every time the
    HA Bluetooth scanner receives a new advertisement (typically 1-2× per
    second), giving near-real-time signal-strength readings.
    """

    _attr_has_entity_name = True
    _attr_name = "Signal Strength"
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: RadiaCodeCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._address: str = entry.data[CONF_ADDRESS]
        self._attr_unique_id = f"{self._address}-{SENSOR_RSSI}"
        self._attr_device_info = build_device_info(
            self._address,
            entry.data.get(CONF_NAME, self._address),
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to BLE advertisements for real-time RSSI updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            bluetooth.async_register_callback(
                self.hass,
                self._handle_bluetooth_update,
                bluetooth.BluetoothCallbackMatcher(
                    address=self._address,
                ),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )

    def _handle_bluetooth_update(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Called on each BLE advertisement — triggers an immediate state write."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Always available — RSSI comes from BT advertisements, not the connection."""
        return True

    @property
    def native_value(self) -> Optional[int]:
        """Return the most recent RSSI value seen by the HA Bluetooth scanner."""
        service_info = bluetooth.async_last_service_info(
            self.hass, self._address, connectable=True
        )
        if service_info is None:
            return None
        return service_info.rssi
