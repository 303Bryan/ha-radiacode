"""DataUpdateCoordinator for the RadiaCode integration.

Poll cycle (every 15 seconds):
  1. Locate the BLE device via HA's Bluetooth manager.
  2. If not already connected, connect and run the device init sequence.
  3. get_data() → fetches data_buf, decodes records.
  4. On error → disconnect (next poll will reconnect).
  5. Merge results with cached RareData values (battery, accumulated_dose appear
     only ~once per minute in RareData records, so we must cache them across
     poll cycles where no RareData is present).

The BLE connection is kept open between polls to avoid the expensive
connect + init round-trip (~7-15 s through ESPHome BT proxies). This
dramatically reduces the data_buf size on each read, making complete
transfers possible within the proxy's notification buffer limit.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_ADDRESS, DOMAIN
from .radiacode_ble import RadiaCodeBLEClient
from .radiacode_ble.protocol import RadiaCodeData

_LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL = timedelta(seconds=15)


class RadiaCodeCoordinator(DataUpdateCoordinator[RadiaCodeData]):
    """Coordinator that polls a RadiaCode device on a fixed schedule."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=_POLL_INTERVAL,
        )
        self._address: str = entry.data[CONF_ADDRESS]
        self._client = RadiaCodeBLEClient()

        # RareData fields appear ~once per minute; cache them so sensors
        # remain valid between polls that contain only RealTimeData records.
        self._last_battery: Optional[float] = None
        self._last_accumulated_dose: Optional[float] = None

    async def _async_update_data(self) -> RadiaCodeData:
        """Fetch data_buf from device, reconnecting only when needed."""

        # Ask HA's Bluetooth stack for the current BLEDevice handle.
        # This works transparently whether the device is on a local adapter
        # or behind an ESPHome Bluetooth proxy.
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(
                f"RadiaCode {self._address} not found — is the device on and in range?"
            )

        try:
            # Reuse existing connection when possible; reconnect if dropped.
            if not self._client.is_connected:
                _LOGGER.debug("RadiaCode not connected, establishing connection")
                await self._client.connect(ble_device)

            data = await self._client.get_data()

        except Exception as err:
            # Tear down stale connection so next poll starts fresh.
            await self._client.disconnect()
            raise UpdateFailed(
                f"Error communicating with RadiaCode {self._address}: {err}"
            ) from err

        # Update cache when fresh RareData arrived in this batch.
        if data.battery is not None:
            self._last_battery = data.battery
        if data.accumulated_dose is not None:
            self._last_accumulated_dose = data.accumulated_dose

        # Return a fully-populated RadiaCodeData using cached values where
        # the device didn't send fresh RareData this cycle.
        return RadiaCodeData(
            dose_rate=data.dose_rate,
            count_rate=data.count_rate,
            accumulated_dose=self._last_accumulated_dose,
            battery=self._last_battery,
        )
