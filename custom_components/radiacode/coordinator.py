"""DataUpdateCoordinator for the RadiaCode integration.

Poll cycle (every 5 seconds):
  1. Locate the BLE device via HA's Bluetooth manager.
  2. If not already connected, connect and run the device init sequence.
  3. get_data() → fetches data_buf, decodes records.
  4. On error → disconnect and retry once (same poll cycle) before giving up.
  5. Merge results with cached RareData values (battery, accumulated_dose appear
     only ~once per minute in RareData records, so we must cache them across
     poll cycles where no RareData is present).

The BLE connection is kept open between polls to avoid the expensive
connect + init round-trip (~7-15 s through ESPHome BT proxies). This
dramatically reduces the data_buf size on each read, making complete
transfers possible within the proxy's notification buffer limit.

Stale connection recovery
─────────────────────────
BLE links through ESPHome proxies can drop silently — the local BleakClient
may still report ``is_connected=True`` while the underlying transport is
dead.  When a get_data() call fails on a connection we *thought* was live,
we disconnect immediately and retry with a fresh connection in the same
poll cycle.  This avoids the 15-second wait-for-next-poll that previously
made the sensor go unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Optional

from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_ADDRESS, DOMAIN
from .radiacode_ble import RadiaCodeBLEClient
from .radiacode_ble.protocol import RadiaCodeData

_LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL = timedelta(seconds=5)

# Seconds to wait after disconnecting before retrying.  The ESPHome BT proxy
# needs time to release the BLE connection slot; without this delay the retry
# hits "slots=0/3 free" and spins for the full connection timeout.
_RETRY_DELAY = 5.0


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
        self._last_temperature: Optional[float] = None

        # Cache dose_rate/count_rate to smooth over reconnection transitions.
        # After a reconnect, the first poll often returns dose_rate=0.0 because
        # the data buffer was just cleared.  We keep the last non-zero values
        # and use them until the device reports real readings.
        self._last_dose_rate: Optional[float] = None
        self._last_count_rate: Optional[float] = None

    async def _async_update_data(self) -> RadiaCodeData:
        """Fetch data_buf from device, reconnecting only when needed.

        If the existing connection turns out to be stale (get_data fails),
        we tear it down and retry once with a fresh connection in the same
        poll cycle.  This prevents the sensor from going unavailable for
        an entire poll interval whenever the BLE link drops silently.
        """

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(
                f"RadiaCode {self._address} not found — is the device on and in range?"
            )

        data = await self._poll_with_retry(ble_device)

        # Update cache when fresh RareData arrived in this batch.
        if data.battery is not None:
            self._last_battery = data.battery
        if data.accumulated_dose is not None:
            self._last_accumulated_dose = data.accumulated_dose
        if data.temperature is not None:
            self._last_temperature = data.temperature

        # Update dose_rate/count_rate cache.  After a reconnect, the first
        # poll yields dose_rate=0.0 because the device's buffer was cleared
        # by the init sequence.  We keep the last meaningful values until
        # the device starts streaming real data again.
        if data.dose_rate is not None and data.dose_rate > 0:
            self._last_dose_rate = data.dose_rate
        if data.count_rate is not None and data.count_rate > 0:
            self._last_count_rate = data.count_rate

        # Determine the best dose_rate/count_rate to expose.  Use fresh
        # values when available, fall back to cache for the post-reconnect
        # zero-value transition.
        dose_rate = data.dose_rate
        count_rate = data.count_rate
        if dose_rate is not None and dose_rate == 0 and self._last_dose_rate is not None:
            dose_rate = self._last_dose_rate
        if count_rate is not None and count_rate == 0 and self._last_count_rate is not None:
            count_rate = self._last_count_rate

        # Return a fully-populated RadiaCodeData using cached values where
        # the device didn't send fresh data this cycle.
        return RadiaCodeData(
            dose_rate=dose_rate,
            count_rate=count_rate,
            accumulated_dose=self._last_accumulated_dose,
            battery=self._last_battery,
            temperature=self._last_temperature,
        )

    async def _poll_with_retry(
        self, ble_device: BLEDevice
    ) -> RadiaCodeData:
        """Connect (if needed), poll, and retry once on failure.

        On the first failure we disconnect and immediately attempt a fresh
        connection + poll.  If that also fails, we propagate the error to
        the DataUpdateCoordinator (which marks the entity unavailable and
        retries on the next poll interval).
        """
        was_connected = self._client.is_connected

        try:
            if not self._client.is_connected:
                _LOGGER.debug("RadiaCode not connected, establishing connection")
                await self._client.connect(ble_device)
            return await self._client.get_data()

        except Exception as first_err:
            # If we were already connected, this is likely a stale-connection
            # failure.  Tear down and retry once with a fresh connection.
            _LOGGER.debug(
                "Poll failed (was_connected=%s): %s — disconnecting and retrying",
                was_connected,
                first_err,
            )
            await self._client.disconnect()

            if not was_connected:
                # Connection attempt itself failed — no point retrying immediately.
                raise UpdateFailed(
                    f"Error connecting to RadiaCode {self._address}: {first_err}"
                ) from first_err

        # ── Retry: fresh connection ─────────────────────────────────────────
        # Give the ESPHome BT proxy time to free the BLE connection slot.
        # Without this, the retry immediately hits "slots=0/3 free" and
        # spins for the full connection timeout before failing.
        _LOGGER.debug(
            "Waiting %.0fs for BT proxy to release connection slot", _RETRY_DELAY
        )
        await asyncio.sleep(_RETRY_DELAY)

        # Re-resolve the BLE device in case the proxy handle changed.
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(
                f"RadiaCode {self._address} not found on retry — is the device on and in range?"
            )

        try:
            _LOGGER.debug("Retry: establishing fresh connection to RadiaCode")
            await self._client.connect(ble_device)
            return await self._client.get_data()
        except Exception as retry_err:
            await self._client.disconnect()
            raise UpdateFailed(
                f"Error communicating with RadiaCode {self._address} (retry also failed): {retry_err}"
            ) from retry_err
