"""Sensor platform for the RadiaCode integration.

Sensors per device:
  • Dose Rate          µSv/h   — real-time ambient radiation dose rate
  • Count Rate         cps     — raw detector counts per second
  • Accumulated Dose   µSv     — total dose since last reset
  • Radiation Alarm    enum    — No Alarm / L1 Alarm / L2 Alarm
  • Battery            %       — device battery level (diagnostic)
  • Temperature        °C      — device temperature (diagnostic)
  • RSSI               dBm     — BLE signal strength from advertisements (diagnostic)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

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
    ALARM_STATE_L1,
    ALARM_STATE_L2,
    ALARM_STATE_NONE,
    CONF_ADDRESS,
    CONF_NAME,
    DOMAIN,
    SENSOR_ACCUMULATED_DOSE,
    SENSOR_BATTERY,
    SENSOR_COUNT_RATE,
    SENSOR_DOSE_RATE,
    SENSOR_RADIATION_ALARM,
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
    entities.append(RadiaCodeRadiationAlarmSensor(coordinator, entry))
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


# Keep showing the last known RSSI for this long after the scanner history
# expires while the device is NOT connected.  While a BLE connection is
# active the device stops advertising entirely, so the cache is used for
# the whole duration of the connection regardless of age.
_RSSI_CACHE_TTL = 15 * 60  # seconds


class RadiaCodeRSSISensor(CoordinatorEntity[RadiaCodeCoordinator], SensorEntity):
    """BLE signal strength (RSSI) reported by the HA Bluetooth scanner.

    RSSI is sourced from BLE advertisement packets.  While the integration
    holds an active BLE connection the device stops advertising, so the HA
    scanner's advertisement history expires and would otherwise read
    "Unknown" for most of the time (the connection is open ~continuously).
    To avoid the intermittent gaps this caused, the sensor caches the last
    observed RSSI and keeps reporting it while the connection is active,
    plus a grace window after the last advertisement when disconnected.

    In addition to the coordinator poll, this entity subscribes to BLE
    advertisement callbacks so the value refreshes whenever the scanner
    actually receives a new advertisement.
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
        self._last_rssi: Optional[int] = None
        self._last_seen: float = 0.0

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
        """Return the most recent RSSI, falling back to the cached value.

        Checks connectable scanner history first, then non-connectable
        (passive proxies).  When the scanner history has expired — which
        is the norm while the device is connected, since a connected BLE
        peripheral does not advertise — the cached value is returned.
        """
        service_info = bluetooth.async_last_service_info(
            self.hass, self._address, connectable=True
        )
        if service_info is None:
            service_info = bluetooth.async_last_service_info(
                self.hass, self._address, connectable=False
            )
        if service_info is not None:
            self._last_rssi = service_info.rssi
            self._last_seen = time.monotonic()
            return service_info.rssi

        if self._last_rssi is None:
            return None
        # No advertisement history: while connected the device can't
        # advertise, so the cache is authoritative.  When disconnected,
        # honour the cache only within the grace window.
        if self.coordinator.is_ble_connected:
            return self._last_rssi
        if time.monotonic() - self._last_seen <= _RSSI_CACHE_TTL:
            return self._last_rssi
        return None


# Device alarm thresholds are stored in µR/h; dose rate is µSv/h.
_uR_PER_uSv = 100.0


class RadiaCodeRadiationAlarmSensor(
    CoordinatorEntity[RadiaCodeCoordinator], SensorEntity
):
    """Enum sensor mirroring the device's dose rate alarm state.

    States: "No Alarm", "L1 Alarm", "L2 Alarm" — compared against the
    device's own L1/L2 dose rate thresholds, so HA alarms even if device
    sound/vibration are switched off.  Both thresholds (converted to
    µSv/h) are exposed as attributes for automations.

    Replaces the Safe/Unsafe binary sensor from 1.0.0; an enum sensor can
    represent the L1/L2 distinction directly in its state.
    """

    _attr_has_entity_name = True
    _attr_name = "Radiation Alarm"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [ALARM_STATE_NONE, ALARM_STATE_L1, ALARM_STATE_L2]

    def __init__(
        self,
        coordinator: RadiaCodeCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{entry.data[CONF_ADDRESS]}-{SENSOR_RADIATION_ALARM}"
        )
        self._attr_device_info = build_device_info(
            entry.data[CONF_ADDRESS],
            entry.data.get(CONF_NAME, entry.data[CONF_ADDRESS]),
        )

    def _thresholds_uSv_h(self) -> tuple[Optional[float], Optional[float]]:
        """Return the (L1, L2) alarm thresholds in µSv/h, or None if unknown."""
        if self.coordinator.data is None:
            return None, None
        settings = self.coordinator.data.settings
        l1 = settings.dr_alarm_level1
        l2 = settings.dr_alarm_level2
        return (
            l1 / _uR_PER_uSv if l1 is not None else None,
            l2 / _uR_PER_uSv if l2 is not None else None,
        )

    @property
    def native_value(self) -> Optional[str]:
        """Return the current alarm state."""
        if self.coordinator.data is None:
            return None
        dose_rate = self.coordinator.data.sensors.dose_rate
        l1, l2 = self._thresholds_uSv_h()
        if dose_rate is None or l1 is None or l1 <= 0:
            return None
        if l2 is not None and l2 > 0 and dose_rate >= l2:
            return ALARM_STATE_L2
        if dose_rate >= l1:
            return ALARM_STATE_L1
        return ALARM_STATE_NONE

    @property
    def icon(self) -> str:
        """Reflect the alarm state in the icon."""
        state = self.native_value
        if state == ALARM_STATE_L2:
            return "mdi:alert-octagram"
        if state == ALARM_STATE_L1:
            return "mdi:alert"
        return "mdi:shield-check"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the alarm thresholds for automations."""
        l1, l2 = self._thresholds_uSv_h()
        attrs: dict[str, Any] = {}
        if l1 is not None:
            attrs["level1_threshold_usv_h"] = l1
        if l2 is not None:
            attrs["level2_threshold_usv_h"] = l2
        return attrs
