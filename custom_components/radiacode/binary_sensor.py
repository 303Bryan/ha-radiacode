"""Binary sensor platform for the RadiaCode integration.

One binary sensor per device:
  • Connectivity — True when the BLE link is active (diagnostic)
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BINARY_SENSOR_CONNECTIVITY, CONF_ADDRESS, CONF_NAME, DOMAIN
from .coordinator import RadiaCodeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RadiaCode binary sensors from a config entry."""
    coordinator: RadiaCodeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RadiaCodeConnectivitySensor(coordinator, entry)])


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
        """Always available — connection state is knowable regardless of poll success."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True when the BLE link to the device is active."""
        return self.coordinator.is_ble_connected
