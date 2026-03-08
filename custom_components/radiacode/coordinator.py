"""DataUpdateCoordinator for the RadiaCode integration.

Poll cycle (every 5 seconds):
  1. If already connected, skip BLE device lookup and poll directly.
  2. If not connected, locate the BLE device via HA's Bluetooth manager
     and connect + run the device init sequence.
  3. get_data() → fetches data_buf, decodes records.
  4. On error → disconnect and retry once (same poll cycle) before giving up.
  5. Merge results with cached RareData values (battery, accumulated_dose appear
     only ~once per minute in RareData records, so we must cache them across
     poll cycles where no RareData is present).

The BLE connection is kept open between polls to avoid the expensive
connect + init round-trip (~7-15 s through ESPHome BT proxies). This
dramatically reduces the data_buf size on each read, making complete
transfers possible within the proxy's notification buffer limit.

BLE device lookup
─────────────────
The ``async_ble_device_from_address`` lookup is only performed when we
need to establish a new connection.  Doing this unconditionally on every
poll caused false "not found" errors when the HA Bluetooth scanner
hadn't seen a recent advertisement — even though the BLE connection was
perfectly healthy.

Stale connection recovery
─────────────────────────
BLE links through ESPHome proxies can drop silently — the local BleakClient
may still report ``is_connected=True`` while the underlying transport is
dead.  When a get_data() call fails on a connection we *thought* was live,
we disconnect immediately and retry with a fresh connection in the same
poll cycle.  This avoids the 15-second wait-for-next-poll that previously
made the sensor go unavailable.

User-controlled BLE connection
──────────────────────────────
The BLE Connection switch lets the user release the device for other
clients (e.g. the RadiaCode mobile app).  When the switch is turned OFF,
``_user_disconnected`` is set True and the BLE link is torn down.  The
``_poll_with_retry`` method checks this flag at 5 checkpoints (before
connect, after connect, before retry, after retry delay, after retry
connect) so that a long-running poll cycle bails out promptly instead
of continuing to connect/retry for 30–60 seconds.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_ADDRESS, DOMAIN
from .radiacode_ble import RadiaCodeBLEClient
from .radiacode_ble.protocol import RadiaCodeData, RadiaCodeSettings

_LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL = timedelta(seconds=5)

# Seconds to wait after disconnecting before retrying.  The ESPHome BT proxy
# needs time to release the BLE connection slot; without this delay the retry
# hits "slots=0/3 free" and spins for the full connection timeout.
_RETRY_DELAY = 2.0


@dataclass
class RadiaCodeCoordinatorData:
    """Combined data container returned by the coordinator.

    Entities use ``.sensors`` for read-only sensor values and ``.settings``
    for writable device configuration.
    """
    sensors: RadiaCodeData
    settings: RadiaCodeSettings


class RadiaCodeCoordinator(DataUpdateCoordinator[RadiaCodeCoordinatorData]):
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

        # Cache device settings; updated every poll, kept on read failure.
        self._last_settings: RadiaCodeSettings = RadiaCodeSettings()

        # Set to True when the user explicitly disconnects via the connection
        # switch.  Polling is suspended until the user turns it back on.
        self._user_disconnected: bool = False

        # ── Device identity (fetched once on first successful connection) ──
        self._serial_number: Optional[str] = None
        self._fw_version: Optional[str] = None

        # ── Diagnostics ──────────────────────────────────────────────────
        self._last_error: Optional[str] = None
        self._last_poll_duration: Optional[float] = None
        self._connection_count: int = 0

    @property
    def user_disconnected(self) -> bool:
        """True when the user has explicitly disabled the BLE connection."""
        return self._user_disconnected

    @property
    def is_ble_connected(self) -> bool:
        """True when the BLE link to the device is currently active."""
        return self._client.is_connected

    @property
    def last_error(self) -> Optional[str]:
        """The last error message, or None if the last poll succeeded."""
        return self._last_error

    @property
    def last_poll_duration(self) -> Optional[float]:
        """Duration of the last poll cycle in seconds (wall-clock)."""
        return self._last_poll_duration

    @property
    def connection_count(self) -> int:
        """Number of successful BLE connections since HA startup."""
        return self._connection_count

    async def async_user_disconnect(self) -> None:
        """Disconnect BLE and suspend polling (user action).

        Marks the integration as user-disconnected so the next poll cycle
        (and all subsequent ones) are skipped without error.  Sensors keep
        their last known values; the connection switch shows OFF.
        """
        self._user_disconnected = True
        await self._client.disconnect()
        # Notify listeners immediately so the switch state updates in the UI.
        self.async_update_listeners()

    async def async_user_reconnect(self) -> None:
        """Resume BLE polling (user action).

        Clears the user-disconnected flag and triggers an immediate refresh,
        which will re-establish the BLE connection on the next poll cycle.
        """
        self._user_disconnected = False
        await self.async_request_refresh()

    async def _async_update_data(self) -> RadiaCodeCoordinatorData:
        """Fetch data_buf from device, reconnecting only when needed.

        If the existing connection turns out to be stale (get_data fails),
        we tear it down and retry once with a fresh connection in the same
        poll cycle.  This prevents the sensor from going unavailable for
        an entire poll interval whenever the BLE link drops silently.
        """

        # When the user has explicitly disabled the connection, skip polling
        # and let UpdateFailed mark all sensor entities unavailable — the
        # data is intentionally stale and should not be shown as current.
        if self._user_disconnected:
            self._last_error = "BLE connection disabled by user"
            raise UpdateFailed(self._last_error)

        poll_start = time.monotonic()

        # Only look up the BLE device when we need to establish a new
        # connection.  When already connected, skip the lookup — it can
        # return None if the HA scanner hasn't received a recent
        # advertisement, which would falsely mark the sensor unavailable
        # even though we have a healthy active connection.
        ble_device = None
        if not self._client.is_connected:
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self._address, connectable=True
            )
            if ble_device is None:
                self._last_error = (
                    f"RadiaCode {self._address} not found — "
                    f"is the device on and in range?"
                )
                raise UpdateFailed(self._last_error)

        try:
            data = await self._poll_with_retry(ble_device)
        except UpdateFailed:
            self._last_poll_duration = time.monotonic() - poll_start
            raise

        # If the user toggled the connection switch OFF during the poll
        # (and the poll still succeeded), tear down the connection now
        # rather than keeping it alive for another 5-second cycle.
        if self._user_disconnected:
            await self._client.disconnect()
            self._last_error = "BLE connection disabled by user"
            self._last_poll_duration = time.monotonic() - poll_start
            raise UpdateFailed(self._last_error)

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

        # Build sensor snapshot with cached fallbacks.
        sensors = RadiaCodeData(
            dose_rate=dose_rate,
            count_rate=count_rate,
            accumulated_dose=self._last_accumulated_dose,
            battery=self._last_battery,
            temperature=self._last_temperature,
        )

        # Read device settings — small BLE response (~60 bytes).
        # On failure, keep the last known values so entity states stay valid.
        try:
            self._last_settings = await self._client.get_settings()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Settings read failed, using cached values: %s", err)

        # Fetch serial number and firmware version once per connection
        # lifecycle.  These are static — they never change while the device
        # is running — so we only read them on the first successful poll
        # after a fresh connection and then update the HA device registry.
        if self._serial_number is None and self._client.is_connected:
            await self._fetch_device_identity()

        self._last_error = None
        self._last_poll_duration = time.monotonic() - poll_start

        return RadiaCodeCoordinatorData(
            sensors=sensors,
            settings=self._last_settings,
        )

    async def _fetch_device_identity(self) -> None:
        """Fetch serial number and firmware version, then update the registry.

        Called once after the first successful BLE poll.  On failure (e.g.
        intermittent BLE glitch) the values stay None and we retry on the
        next poll cycle.
        """
        try:
            self._serial_number = await self._client.get_serial_number()
            self._fw_version = await self._client.get_firmware_version()
            _LOGGER.debug(
                "Device identity: serial=%s  firmware=%s",
                self._serial_number,
                self._fw_version,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch serial/firmware (will retry next poll)")
            self._serial_number = None  # ensure we retry
            return

        # Push the serial number and firmware version into the HA device
        # registry so they appear on the device info card.
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self._address)})
        if device is not None:
            registry.async_update_device(
                device.id,
                serial_number=self._serial_number,
                sw_version=self._fw_version,
            )

    def _check_user_disconnected(self, context: str = "") -> None:
        """Raise UpdateFailed if the user has disabled the BLE connection.

        Called at checkpoints inside ``_poll_with_retry`` so a long-running
        poll cycle bails out promptly when the user flips the connection
        switch OFF, rather than continuing to connect/retry for 30-60 s.
        """
        if self._user_disconnected:
            _LOGGER.debug("User disabled BLE — aborting poll (%s)", context)
            self._last_error = "BLE connection disabled by user"
            raise UpdateFailed(self._last_error)

    async def _poll_with_retry(
        self, ble_device: Optional[BLEDevice]
    ) -> RadiaCodeData:
        """Connect (if needed), poll, and retry once on failure.

        ``ble_device`` may be None when the client is already connected
        (the caller skips the BLE lookup in that case).

        On the first failure we disconnect and immediately attempt a fresh
        connection + poll.  The retry re-resolves the BLE device, which
        often selects a different ESPHome BT proxy with a better signal.
        If the retry also fails, we propagate the error to the
        DataUpdateCoordinator (which marks the entity unavailable and
        retries on the next poll interval).

        Multiple ``_check_user_disconnected()`` checkpoints ensure that if
        the user disables the BLE connection switch during a long poll
        cycle, we bail out at the next checkpoint instead of continuing to
        connect/retry for 30+ seconds.
        """
        # ── Checkpoint 1: bail before any work ──────────────────────────────
        self._check_user_disconnected("before connect")

        was_connected = self._client.is_connected

        try:
            if not self._client.is_connected:
                if ble_device is None:
                    self._last_error = (
                        f"RadiaCode {self._address} not found — "
                        f"is the device on and in range?"
                    )
                    raise UpdateFailed(self._last_error)
                _LOGGER.debug("RadiaCode not connected, establishing connection")
                await self._client.connect(ble_device)
                self._connection_count += 1

                # ── Checkpoint 2: user may have toggled during connect() ────
                # connect() can take 15+ s through an ESPHome BT proxy.  If
                # the user disabled BLE in that window, disconnect the freshly
                # established link and bail immediately.
                if self._user_disconnected:
                    _LOGGER.debug(
                        "User disabled BLE during connect — tearing down"
                    )
                    await self._client.disconnect()
                    self._last_error = "BLE connection disabled by user"
                    raise UpdateFailed(self._last_error)

            return await self._client.get_data()

        except UpdateFailed:
            raise

        except Exception as first_err:
            _LOGGER.debug(
                "Poll failed (was_connected=%s): %s — disconnecting and retrying",
                was_connected,
                first_err,
            )
            await self._client.disconnect()

        # ── Checkpoint 3: bail before retry if user disabled ────────────────
        self._check_user_disconnected("before retry")

        # ── Retry: fresh connection ─────────────────────────────────────────
        # Always retry once — the re-resolved BLE device may route through a
        # different proxy with a better signal.  The old was_connected guard
        # prevented this, causing the device to go unavailable when the
        # initial connection's init sequence failed through one proxy but
        # would have succeeded through another.
        #
        # Give the ESPHome BT proxy time to free the BLE connection slot.
        # Without this, the retry immediately hits "slots=0/3 free" and
        # spins for the full connection timeout before failing.
        _LOGGER.debug(
            "Waiting %.0fs for BT proxy to release connection slot", _RETRY_DELAY
        )
        await asyncio.sleep(_RETRY_DELAY)

        # ── Checkpoint 4: bail after delay if user disabled ─────────────────
        self._check_user_disconnected("after retry delay")

        # Re-resolve the BLE device in case the proxy handle changed.
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if ble_device is None:
            self._last_error = (
                f"RadiaCode {self._address} not found on retry — "
                f"is the device on and in range?"
            )
            raise UpdateFailed(self._last_error)

        try:
            _LOGGER.debug("Retry: establishing fresh connection to RadiaCode")
            await self._client.connect(ble_device)
            self._connection_count += 1

            # ── Checkpoint 5: user may have toggled during retry connect ────
            if self._user_disconnected:
                _LOGGER.debug(
                    "User disabled BLE during retry connect — tearing down"
                )
                await self._client.disconnect()
                self._last_error = "BLE connection disabled by user"
                raise UpdateFailed(self._last_error)

            return await self._client.get_data()

        except UpdateFailed:
            raise

        except Exception as retry_err:
            await self._client.disconnect()
            self._last_error = (
                f"Error communicating with RadiaCode {self._address} "
                f"(retry also failed): {retry_err}"
            )
            raise UpdateFailed(self._last_error) from retry_err

    async def async_write_setting(self, vsfr_id: int, value: int) -> None:
        """Write a single device setting register and refresh data.

        Raises UpdateFailed if the write fails or the device rejects the value.
        """
        if not self._client.is_connected:
            raise UpdateFailed("Cannot write setting: device not connected")

        try:
            ok = await self._client.write_vsfr(vsfr_id, value)
        except Exception as err:
            raise UpdateFailed(
                f"Failed to write VSFR {vsfr_id:#06x}={value}: {err}"
            ) from err

        if not ok:
            raise UpdateFailed(
                f"Device rejected VSFR write {vsfr_id:#06x}={value}"
            )

        # Trigger an immediate refresh so the new value shows up in the UI.
        await self.async_request_refresh()
