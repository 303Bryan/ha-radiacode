"""Binary sensor platform for the RadiaCode integration.

Two binary sensors per device:
  • Connectivity     — True when the BLE link is active (diagnostic)
  • Radiation Alarm  — True when the dose rate exceeds the device's
                       L1 alarm threshold (level 2 exposed as attribute)
"""

from __future__ import annotations

from typing import Any, Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BINARY_SENSOR_CONNECTIVITY,
    BINARY_SENSOR_RADIATION_ALARM,
    CONF_ADDRESS,
    CONF_NAME,
    DOMAIN,
    build_device_info,
)
from .coordinator import RadiaCodeCoordinator

# Device alarm thresholds are stored in µR/h; sensors report µSv/h.
_uR_PER_uSv = 100.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RadiaCode binary sensors from a config entry."""
    coordinator: RadiaCodeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            RadiaCodeConnectivitySensor(coordinator, entry),
            RadiaCodeRadiationAlarmSensor(coordinator, entry),
        ]
    )


class RadiaCodeConnectivitySensor(
    CoordinatorEntity[RadiaCodeCoordinator], BinarySensorEntity
):
    """Binary sensor showing the active BLE connection state.

    Reflects whether the BLE link is currently established.  Unlike the
    BLE Connection switch (which is a user control), this is a read-only
    diagnostic that reports the true underlying connection state.

    This sensor is always available in HA — it can show "Disconnected"
    even when the coordinator has no fresh data.
    """

    _attr_has_entity_name = True
    _attr_name = "BLE Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: RadiaCodeCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_ADDRESS]}-{BINARY_SENSOR_CONNECTIVITY}"
        self._attr_device_info = build_device_info(
            entry.data[CONF_ADDRESS],
            entry.data.get(CONF_NAME, entry.data[CONF_ADDRESS]),
        )

    @property
    def available(self) -> bool:
        """Always available — connection state is knowable regardless of poll success."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True when the BLE link to the device is active."""
        return self.coordinator.is_ble_connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose connection diagnostics as entity attributes."""
        attrs: dict[str, Any] = {
            "connection_count": self.coordinator.connection_count,
        }
        last_err = self.coordinator.last_error
        if last_err is not None:
            attrs["last_error"] = last_err
        poll_dur = self.coordinator.last_poll_duration
        if poll_dur is not None:
            attrs["last_poll_duration"] = round(poll_dur, 3)
        return attrs


class RadiaCodeRadiationAlarmSensor(
    CoordinatorEntity[RadiaCodeCoordinator], BinarySensorEntity
):
    """Binary sensor that mirrors the device's dose rate alarm.

    Turns ON when the current dose rate reaches the device's L1 alarm
    threshold.  The active alarm level (0, 1, or 2) and both thresholds
    (converted to µSv/h) are exposed as attributes, so automations can
    distinguish a level-2 alarm from level-1.

    The comparison is computed locally from the same values the device
    uses, so HA alarms even if device sound/vibration are switched off.
    """

    _attr_has_entity_name = True
    _attr_name = "Radiation Alarm"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(
        self,
        coordinator: RadiaCodeCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{entry.data[CONF_ADDRESS]}-{BINARY_SENSOR_RADIATION_ALARM}"
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
    def _alarm_level(self) -> Optional[int]:
        """Current alarm level: 0 (clear), 1, or 2.  None when unknown."""
        if self.coordinator.data is None:
            return None
        dose_rate = self.coordinator.data.sensors.dose_rate
        l1, l2 = self._thresholds_uSv_h()
        if dose_rate is None or l1 is None or l1 <= 0:
            return None
        if l2 is not None and l2 > 0 and dose_rate >= l2:
            return 2
        if dose_rate >= l1:
            return 1
        return 0

    @property
    def is_on(self) -> Optional[bool]:
        """Return True when the dose rate meets or exceeds the L1 threshold."""
        level = self._alarm_level
        if level is None:
            return None
        return level >= 1

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose alarm level and thresholds for automations."""
        l1, l2 = self._thresholds_uSv_h()
        attrs: dict[str, Any] = {}
        if (level := self._alarm_level) is not None:
            attrs["alarm_level"] = level
        if l1 is not None:
            attrs["level1_threshold_usv_h"] = l1
        if l2 is not None:
            attrs["level2_threshold_usv_h"] = l2
        return attrs
