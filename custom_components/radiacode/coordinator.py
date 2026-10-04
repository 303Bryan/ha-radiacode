"""DataUpdateCoordinator for the RadiaCode integration.

Primary poll cycle (every 5 seconds by default):
  1. If already connected, skip BLE device lookup and poll directly.
  2. If not connected, locate the BLE device via HA's Bluetooth manager
     and connect + run the device init sequence.
  3. get_data() → fetches data_buf, decodes records.
  4. On error → disconnect and retry once (same poll cycle) before giving up.
  5. Merge results with cached RareData values (battery, accumulated_dose appear
     only ~once per minute in RareData records, so we must cache them across
     poll cycles where no RareData is present).
  6. Return the radiation snapshot to HA before starting optional maintenance.

One managed background task reads identity, settings, health, temperature and
spectrum on independent elapsed-time cadences. Maintenance updates cached
fields/listeners without resetting the primary timer or radiation availability.
Repeated/empty buffers only retain a paired radiation sample for a bounded grace
period; they never renew its freshness.

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
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Optional

from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ADDRESS,
    CONF_POLL_INTERVAL,
    CONF_SPECTRUM_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SPECTRUM_INTERVAL,
    DOMAIN,
)
from .radiacode_ble import RadiaCodeBLEClient, RadiaCodeInitError
from .radiacode_ble.protocol import (
    VSFR,
    RadiaCodeData,
    RadiaCodeDiagnostics,
    RadiaCodeSettings,
    Spectrum,
    SpikeFilter,
    compute_hardness,
)

_LOGGER = logging.getLogger(__name__)

# Seconds to wait after disconnecting before retrying.  The ESPHome BT proxy
# needs time to release the BLE connection slot; without this delay the retry
# hits "slots=0/3 free" and spins for the full connection timeout.
_RETRY_DELAY = 2.0


# Settings and device health change slowly. Keep these reads off the fast
# radiation polling path; use elapsed time so changing the poll interval
# doesn't inadvertently increase Bluetooth traffic.
_SETTINGS_INTERVAL = 60.0
_DIAGNOSTICS_INTERVAL = 60.0
_IDENTITY_RETRY_INTERVAL = 60.0
_TEMPERATURE_INTERVAL = 60.0
_SPECTRUM_RETRY_MIN = 300.0
_SPECTRUM_RETRY_MAX = 3600.0
_SPECTRUM_FAILURE_LIMIT = 3
_PHASE_HISTORY_LIMIT = 20


@dataclass
class RadiaCodeCoordinatorData:
    """Combined data container returned by the coordinator.

    Entities use ``.sensors`` for read-only sensor values, ``.settings``
    for writable device configuration, and ``.diagnostics`` for
    device-health readings.
    """
    sensors: RadiaCodeData
    settings: RadiaCodeSettings
    diagnostics: RadiaCodeDiagnostics = field(default_factory=RadiaCodeDiagnostics)
    spectrum: Optional[Spectrum] = None


class RadiaCodeCoordinator(DataUpdateCoordinator[RadiaCodeCoordinatorData]):
    """Coordinator that polls a RadiaCode device on a fixed schedule."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=poll_interval),
        )

        self._entry_id = entry.entry_id
        # Spectrum polling cadence in elapsed seconds. 0 disables.
        spectrum_interval = entry.options.get(
            CONF_SPECTRUM_INTERVAL, DEFAULT_SPECTRUM_INTERVAL
        )
        self._spectrum_interval = float(spectrum_interval)
        self._next_spectrum_read = 0.0
        self._spectrum_retry_delay = max(_SPECTRUM_RETRY_MIN, self._spectrum_interval)
        self._last_spectrum_error: Optional[str] = None
        self._spectrum_consecutive_failures = 0
        self._automatic_spectrum_paused = False
        self._address: str = entry.data[CONF_ADDRESS]
        self._client = RadiaCodeBLEClient()

        # RareData fields appear ~once per minute; cache them so sensors
        # remain valid between polls that contain only RealTimeData records.
        self._last_battery: Optional[float] = None
        self._last_accumulated_dose: Optional[float] = None
        self._last_temperature: Optional[float] = None

        # Keep one accepted measurement pair across brief record-less polls.
        # A valid zero is a fresh measurement, not a reconnect placeholder.
        self._last_dose_rate: Optional[float] = None
        self._last_count_rate: Optional[float] = None
        self._last_measurement_time: Optional[datetime] = None
        self._last_measurement_type: Optional[str] = None
        self._last_measurement_flags: Optional[int] = None
        self._last_fresh_monotonic: Optional[float] = None
        self._last_measurement_connection = 0
        # Three normal polling opportunities, with a 60-second minimum,
        # cover proxy reconnect/init delays without hiding a stalled stream.
        self._freshness_grace = max(60.0, float(poll_interval) * 3)
        self._measurement_expiry_handle: Optional[asyncio.TimerHandle] = None
        self._using_cached_measurement = False
        self._last_sample_progress: dict = {}

        # Outlier suppression: truncated BT-proxy transfers can occasionally
        # yield a misparsed record with an absurd value (e.g. 40 000 µSv/h at
        # background).  A reading far above baseline is held back one poll
        # and only accepted if the next poll confirms it — genuine radiation
        # events are sustained, corrupt values are one-off.
        self._dose_rate_filter = SpikeFilter(factor=50.0, pass_below=5.0)
        self._count_rate_filter = SpikeFilter(factor=50.0, pass_below=100.0)

        # Cache device settings between minute reads and keep them on failure.
        self._last_settings: RadiaCodeSettings = RadiaCodeSettings()
        self._next_settings_read = 0.0

        # Device-health diagnostics are cached between once-per-minute reads.
        self._last_diagnostics: RadiaCodeDiagnostics = RadiaCodeDiagnostics()
        self._next_diagnostics_read = 0.0
        self._next_temperature_read = 0.0

        # Gamma spectrum; read on its own slower cadence (options-driven)
        # and cached in between — the heaviest BLE transfer we perform.
        self._last_spectrum: Optional[Spectrum] = None

        # Set to True when the user explicitly disconnects via the connection
        # switch.  Polling is suspended until the user turns it back on.
        self._user_disconnected: bool = False

        # ── Device identity (fetched once on first successful connection) ──
        self._serial_number: Optional[str] = None
        self._fw_version: Optional[str] = None
        self._next_identity_read = 0.0

        # Device's self-describing SFR register directory (fetched once,
        # alongside identity).  Logged at debug and exposed in diagnostics.
        self._sfr_file: Optional[str] = None

        # ── Diagnostics ──────────────────────────────────────────────────
        self._last_error: Optional[str] = None
        self._last_poll_duration: Optional[float] = None
        self._connection_count: int = 0
        self._connection_source: Optional[str] = None
        self._connection_rssi: Optional[int] = None
        self._maintenance_task: Optional[asyncio.Task] = None
        self._maintenance_phase: Optional[str] = None
        self._stopping = False
        self._primary_idle = asyncio.Event()
        self._primary_idle.set()
        self._optional_errors: dict[str, str] = {}
        self._phase_history: deque[dict] = deque(maxlen=_PHASE_HISTORY_LIMIT)
        self._last_spectrum_attempt: Optional[float] = None
        self._last_spectrum_success: Optional[float] = None

    @property
    def address(self) -> str:
        """The configured Bluetooth address of this device."""
        return self._address

    @property
    def user_disconnected(self) -> bool:
        """True when the user has explicitly disabled the BLE connection."""
        return self._user_disconnected

    @property
    def is_ble_connected(self) -> bool:
        """True when the BLE link to the device is currently active."""
        return self._client.is_connected

    @property
    def measurement_available(self) -> bool:
        """Keep a known sample available briefly while the BLE link recovers.

        Acquisition failures remain visible through ``last_update_success``
        and diagnostics. Only a newly accepted paired reading renews this
        lease; cached, repeated and filtered records cannot extend it.
        """
        age = self._age(self._last_fresh_monotonic)
        return (
            not self._stopping and not self._user_disconnected
            and self.data is not None
            and age is not None and age < self._freshness_grace
        )

    def _cancel_measurement_expiry(self) -> None:
        """Cancel the callback attached to the previous accepted sample."""
        if self._measurement_expiry_handle is not None:
            self._measurement_expiry_handle.cancel()
            self._measurement_expiry_handle = None

    def _schedule_measurement_expiry(self) -> None:
        """Expire sensor states even while reconnect or maintenance is blocked."""
        self._cancel_measurement_expiry()
        age = self._age(self._last_fresh_monotonic)
        if self._stopping or self._user_disconnected or age is None:
            return
        self._measurement_expiry_handle = asyncio.get_running_loop().call_later(
            max(0.001, self._freshness_grace - age), self._expire_measurement,
        )

    def _expire_measurement(self) -> None:
        """Notify listeners when the measurement lease ends, without polling."""
        self._measurement_expiry_handle = None
        if self._stopping or self._user_disconnected:
            return
        age = self._age(self._last_fresh_monotonic)
        # An early timer must re-arm; it cannot leave the old state available.
        if age is not None and age < self._freshness_grace:
            self._schedule_measurement_expiry()
            return
        self.async_update_listeners()

    @property
    def last_error(self) -> Optional[str]:
        """Primary acquisition status; optional failures are reported separately."""
        return self._last_error

    @property
    def last_poll_duration(self) -> Optional[float]:
        """Duration of the last poll cycle in seconds (wall-clock)."""
        return self._last_poll_duration

    @property
    def connection_count(self) -> int:
        """Number of successful BLE connections since HA startup."""
        return self._connection_count

    @property
    def serial_number(self) -> Optional[str]:
        """Device serial number, or None until the first successful poll."""
        return self._serial_number

    @property
    def firmware_version(self) -> Optional[str]:
        """Device firmware version, or None until the first successful poll."""
        return self._fw_version

    @property
    def sfr_file(self) -> Optional[str]:
        """The device's SFR register directory text, or None if unread."""
        return self._sfr_file

    @property
    def spectrum_status(self) -> dict:
        """Summarize spectrum polling without copying the large histogram."""
        return {
            "poll_interval": self._spectrum_interval,
            "automatic_paused": self._automatic_spectrum_paused,
            "consecutive_failures": self._spectrum_consecutive_failures,
            "failure_limit": _SPECTRUM_FAILURE_LIMIT,
            "format_version": self._client.spectrum_format_version,
            "format_source": getattr(self._client, "spectrum_format_source", None),
            "last_error": self._last_spectrum_error,
            "channel_count": len(self._last_spectrum.counts) if self._last_spectrum else 0,
            "duration_s": self._last_spectrum.duration_s if self._last_spectrum else None,
            "truncated": self._last_spectrum.truncated if self._last_spectrum else None,
            "retry_in_seconds": max(0.0, self._next_spectrum_read - time.monotonic())
            if self._spectrum_interval > 0 and not self._automatic_spectrum_paused
            else None,
            "last_attempt_age_seconds": self._age(self._last_spectrum_attempt),
            "last_success_age_seconds": self._age(self._last_spectrum_success),
        }

    @staticmethod
    def _age(timestamp: Optional[float]) -> Optional[float]:
        """Return an elapsed age without depending on the device/host clock."""
        return max(0.0, time.monotonic() - timestamp) if timestamp is not None else None

    @property
    def runtime_status(self) -> dict:
        """Bounded polling/maintenance diagnostics, without histogram copies."""
        age = self._age(self._last_fresh_monotonic)
        return {
            "freshness": {
                "age_seconds": age,
                "grace_seconds": self._freshness_grace,
                "fresh": age is not None and age < self._freshness_grace,
                "using_cached_measurement": self._last_fresh_monotonic is not None
                and (self._using_cached_measurement or not self.last_update_success),
                "measurement_time": self._last_measurement_time.isoformat()
                if self._last_measurement_time is not None else None,
                "measurement_type": self._last_measurement_type,
                "measurement_flags": self._last_measurement_flags,
                "sample_progress": dict(self._last_sample_progress),
            },
            "bluetooth": {
                "advertisement_source_at_connect": self._connection_source,
                "advertisement_rssi_at_connect": self._connection_rssi,
            },
            "maintenance": {
                "running": self._maintenance_task is not None
                and not self._maintenance_task.done(),
                "phase": self._maintenance_phase,
                "errors": dict(self._optional_errors),
            },
            "recent_phases": list(self._phase_history),
        }

    @property
    def transport_status(self) -> dict:
        """Expose the client's bounded transport diagnostics for downloads."""
        return self._client.transport_diagnostics

    async def async_shutdown(self) -> None:
        """Stop polling and tear down the BLE connection.

        Called on config entry unload and on Home Assistant shutdown so
        the device is released for other BLE clients (e.g. the mobile app).
        """
        self._stopping = True
        self._cancel_measurement_expiry()
        await super().async_shutdown()
        await self._cancel_maintenance()
        await self._client.disconnect()

    async def async_user_disconnect(self) -> None:
        """Disconnect BLE and suspend polling (user action).

        Marks the integration as user-disconnected so the next poll cycle
        (and all subsequent ones) are skipped without error.  Sensors keep
        their last known values; the connection switch shows OFF.
        """
        self._user_disconnected = True
        self._cancel_measurement_expiry()
        # Update the switch and invalidate the sample before slow teardown.
        self.async_update_listeners()
        await self._cancel_maintenance()
        await self._client.disconnect()
        # Publish the final physical BLE state after releasing the transport.
        self.async_update_listeners()

    async def async_user_reconnect(self) -> None:
        """Resume BLE polling (user action).

        Clears the user-disconnected flag and triggers an immediate refresh,
        which will re-establish the BLE connection on the next poll cycle.
        """
        self._user_disconnected = False
        self._next_spectrum_read = 0.0
        await self.async_request_refresh()

    async def _cancel_maintenance(self) -> None:
        """Cancel and await the only maintenance task before releasing BLE."""
        task = self._maintenance_task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Awaiting a cancelled child normally raises CancelledError. If
            # our caller was cancelled as well, preserve that cancellation.
            if (caller := asyncio.current_task()) is not None and caller.cancelling():
                raise
        finally:
            if self._maintenance_task is task:
                self._maintenance_task = None
            self._maintenance_phase = None

    def _record_phase(
        self, phase: str, started: float, error: Optional[str] = None
    ) -> None:
        duration = time.monotonic() - started
        self._phase_history.append({
            "phase": phase,
            "duration_seconds": round(duration, 3),
            "error": error,
        })
        _LOGGER.debug(
            "Poll phase %s completed in %.3fs (error=%s, connected=%s)",
            phase, duration, error, self._client.is_connected,
        )

    def _remember_connection_source(self) -> None:
        """Remember public advertisement metadata, not selected connection route."""
        service_info = bluetooth.async_last_service_info(
            self.hass, self._address, connectable=True
        )
        self._connection_source = service_info.source if service_info else None
        self._connection_rssi = service_info.rssi if service_info else None
        _LOGGER.debug(
            "Connection lookup source=%s rssi=%s",
            self._connection_source, self._connection_rssi,
        )

    async def _async_update_data(self) -> RadiaCodeCoordinatorData:
        """Publish radiation promptly; slow reads run in one managed task."""
        poll_start = time.monotonic()
        phase_error = None
        self._primary_idle.clear()
        try:
            self._check_user_disconnected("before radiation poll")
            ble_device = None
            if not self._client.is_connected:
                ble_device = bluetooth.async_ble_device_from_address(
                    self.hass, self._address, connectable=True
                )
                if ble_device is None:
                    raise UpdateFailed(
                        "RadiaCode not found — is the device on and in range?"
                    )

            data = await self._poll_with_retry(ble_device)
            self._check_user_disconnected("after radiation poll")
            now = time.monotonic()
            if data.battery is not None:
                self._last_battery = data.battery
            if data.accumulated_dose is not None:
                self._last_accumulated_dose = data.accumulated_dose
            if data.temperature is not None:
                self._last_temperature = data.temperature
                self._next_temperature_read = now + _TEMPERATURE_INTERVAL

            # Empty buffers, repeated timestamps and filtered samples must
            # never reset freshness. Keep both values from one accepted record.
            fresh = (
                data.dose_rate is not None and data.count_rate is not None
                and data.measurement_time is not None
                and (
                    self._last_measurement_time is None
                    or self._last_measurement_connection != self._connection_count
                    or data.measurement_time > self._last_measurement_time
                )
            )
            if fresh:
                dose_rate = self._dose_rate_filter.filter(data.dose_rate)
                count_rate = self._count_rate_filter.filter(data.count_rate)
                for label, filter_ in (
                    ("dose rate", self._dose_rate_filter),
                    ("count rate", self._count_rate_filter),
                ):
                    if filter_.last_suppressed is not None:
                        _LOGGER.warning(
                            "Suppressed suspected %s outlier: %.4g "
                            "(awaiting a subsequent measurement)",
                            label, filter_.last_suppressed,
                        )
                fresh = dose_rate is not None and count_rate is not None
                if fresh:
                    same_connection = (
                        self._last_measurement_connection == self._connection_count
                    )
                    self._last_sample_progress = {
                        "device_seconds": (
                            data.measurement_time - self._last_measurement_time
                        ).total_seconds()
                        if self._last_measurement_time is not None and same_connection
                        else None,
                        "receipt_seconds": self._age(self._last_fresh_monotonic)
                        if same_connection else None,
                    }
                    self._last_dose_rate = dose_rate
                    self._last_count_rate = count_rate
                    self._last_measurement_time = data.measurement_time
                    self._last_measurement_type = data.measurement_type
                    self._last_measurement_flags = data.measurement_flags
                    self._last_measurement_connection = self._connection_count
                    self._last_fresh_monotonic = now
                    self._schedule_measurement_expiry()

            self._using_cached_measurement = not fresh
            # Even an initial empty buffer is a complete transport response.
            # Identity/spectrum discovery may proceed while radiation warms up.
            self._schedule_maintenance()
            age = self._age(self._last_fresh_monotonic)
            if age is None:
                raise UpdateFailed("Waiting for a fresh radiation measurement")
            if age >= self._freshness_grace:
                raise UpdateFailed(
                    f"Radiation measurements are stale ({age:.1f}s since "
                    f"the last fresh sample; grace {self._freshness_grace:.0f}s)"
                )

            self._last_error = None if fresh else "No new radiation measurement"
            sensors = RadiaCodeData(
                dose_rate=self._last_dose_rate,
                count_rate=self._last_count_rate,
                accumulated_dose=self._last_accumulated_dose,
                battery=self._last_battery,
                temperature=self._last_temperature,
                hardness=compute_hardness(
                    self._last_dose_rate, self._last_count_rate
                ),
                measurement_time=self._last_measurement_time,
                measurement_type=self._last_measurement_type,
                measurement_flags=self._last_measurement_flags,
            )
            _LOGGER.debug(
                "Radiation ready: fresh=%s age=%.3fs type=%s timestamp=%s "
                "flags=%s dose_rate=%s count_rate=%s progress=%s",
                fresh, age, self._last_measurement_type,
                self._last_measurement_time, self._last_measurement_flags,
                sensors.dose_rate, sensors.count_rate, self._last_sample_progress,
            )
            return RadiaCodeCoordinatorData(
                sensors=sensors,
                settings=self._last_settings,
                diagnostics=self._last_diagnostics,
                spectrum=self._last_spectrum,
            )
        except asyncio.CancelledError:
            phase_error = "cancelled"
            raise
        except UpdateFailed as err:
            self._last_error = str(err)
            self._using_cached_measurement = self._last_fresh_monotonic is not None
            phase_error = self._last_error
            raise
        finally:
            self._last_poll_duration = time.monotonic() - poll_start
            self._record_phase("radiation", poll_start, phase_error)
            self._primary_idle.set()

    def _schedule_maintenance(self) -> None:
        """Start at most one task, after the current radiation poll returns."""
        if (
            self._stopping or self._user_disconnected
            or not self._client.is_connected
            or (
                self._maintenance_task is not None
                and not self._maintenance_task.done()
            )
        ):
            return
        now = time.monotonic()
        if not (
            (self._serial_number is None and now >= self._next_identity_read)
            or now >= self._next_settings_read
            or now >= self._next_diagnostics_read
            or now >= self._next_temperature_read
            or (
                self._spectrum_interval > 0 and not self._automatic_spectrum_paused
                and now >= self._next_spectrum_read
            )
        ):
            return
        # Non-eager start ensures HA can publish the returned primary snapshot
        # before any optional operation begins waiting for its BLE reply.
        self._maintenance_task = self.hass.async_create_background_task(
            self._async_run_maintenance(),
            name=f"Radiacode maintenance {self._entry_id}",
            eager_start=False,
        )
        self._maintenance_task.add_done_callback(self._maintenance_done)

    def _maintenance_done(self, task: asyncio.Task) -> None:
        if self._maintenance_task is task:
            self._maintenance_task = None
            self._maintenance_phase = None
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOGGER.error(
                "Unexpected maintenance failure",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _async_run_maintenance(self) -> None:
        """Run due operations sequentially, yielding to primary polls."""
        operations = (
            ("identity", "_next_identity_read", _IDENTITY_RETRY_INTERVAL,
             self._fetch_device_identity, None),
            ("settings", "_next_settings_read", _SETTINGS_INTERVAL,
             self._client.get_settings, "_last_settings"),
            ("health", "_next_diagnostics_read", _DIAGNOSTICS_INTERVAL,
             self._client.get_diagnostics, "_last_diagnostics"),
            ("temperature", "_next_temperature_read", _TEMPERATURE_INTERVAL,
             self._client.get_temperature, "_last_temperature"),
            ("spectrum", "_next_spectrum_read", self._spectrum_interval,
             self._client.get_spectrum, "_last_spectrum"),
        )
        try:
            for phase, deadline, interval, operation, cache_attribute in operations:
                # Yield between commands. A scheduled radiation poll gets the
                # next opportunity; the client's lock also serializes controls.
                await asyncio.sleep(0)
                await self._primary_idle.wait()
                if (
                    self._stopping or self._user_disconnected
                    or not self._client.is_connected
                ):
                    return
                now = time.monotonic()
                if now < getattr(self, deadline):
                    continue
                if phase == "identity" and self._serial_number is not None:
                    continue
                if phase == "spectrum" and (
                    self._spectrum_interval <= 0 or self._automatic_spectrum_paused
                ):
                    continue
                setattr(self, deadline, now + interval)
                self._maintenance_phase = phase
                started = now
                error = None
                if phase == "spectrum":
                    self._last_spectrum_attempt = now
                try:
                    result = await operation()
                    if self._stopping or self._user_disconnected:
                        return
                    if cache_attribute is not None and result is not None:
                        setattr(self, cache_attribute, result)
                    self._optional_errors.pop(phase, None)
                    if phase == "spectrum":
                        self._record_spectrum_success(result)
                except asyncio.CancelledError:
                    error = "cancelled"
                    raise
                except Exception as err:  # noqa: BLE001
                    error = str(err)
                    self._optional_errors[phase] = error
                    if phase == "spectrum":
                        self._record_spectrum_failure(error)
                    else:
                        _LOGGER.debug(
                            "Maintenance %s failed; retaining cached values: %s",
                            phase, err,
                        )
                finally:
                    self._record_phase(phase, started, error)
                    self._maintenance_phase = None
                self._publish_maintenance_cache()
        finally:
            self._maintenance_phase = None

    def _publish_maintenance_cache(self) -> None:
        """Notify optional changes without rewriting radiation freshness/cadence."""
        if self._stopping or self._user_disconnected:
            return
        if self.data is not None:
            # Use the latest primary snapshot. A newer primary poll may have
            # completed while this task awaited a BLE response.
            self.data = replace(
                self.data,
                sensors=replace(
                    self.data.sensors, temperature=self._last_temperature
                ),
                settings=self._last_settings,
                diagnostics=self._last_diagnostics,
                spectrum=self._last_spectrum,
            )
        # Do not call async_set_updated_data(): it resets the primary timer and
        # marks a failed/stale radiation poll successful.
        self.async_update_listeners()

    def _record_spectrum_success(self, spectrum: Spectrum) -> None:
        self._last_spectrum = spectrum
        # A successful manual current-spectrum read also proves this path is
        # usable again and resumes automatic reads at the configured cadence.
        self._spectrum_consecutive_failures = 0
        self._automatic_spectrum_paused = False
        self._last_spectrum_error = None
        self._optional_errors.pop("spectrum", None)
        self._last_spectrum_success = time.monotonic()
        self._next_spectrum_read = time.monotonic() + self._spectrum_interval
        self._spectrum_retry_delay = max(
            _SPECTRUM_RETRY_MIN, self._spectrum_interval
        )

    def _record_spectrum_failure(self, error: str) -> None:
        self._last_spectrum_error = error
        self._spectrum_consecutive_failures += 1
        if self._spectrum_consecutive_failures >= _SPECTRUM_FAILURE_LIMIT:
            self._automatic_spectrum_paused = True
            _LOGGER.warning(
                "Automatic spectrum reads paused after %d consecutive failures "
                "to avoid repeated BLE interruptions. Retaining the last complete "
                "spectrum; a successful manual current-spectrum read, reset or an "
                "integration reload resumes automatic reads. Last error: %s",
                self._spectrum_consecutive_failures, error,
            )
            return
        self._next_spectrum_read = time.monotonic() + self._spectrum_retry_delay
        _LOGGER.warning(
            "Spectrum read failed; retaining previous snapshot, retry in %.0fs: %s",
            self._spectrum_retry_delay, error,
        )
        self._spectrum_retry_delay = min(
            _SPECTRUM_RETRY_MAX, self._spectrum_retry_delay * 2
        )

    async def _fetch_device_identity(self) -> None:
        """Fetch serial number and firmware version, then update the registry.

        Called once after the first successful BLE poll.  On failure (e.g.
        intermittent BLE glitch) the values stay None and we retry after
        the identity read cooldown.
        """
        try:
            self._serial_number = await self._client.get_serial_number()
            self._check_user_disconnected("after serial number")
            self._fw_version = await self._client.get_firmware_version()
            self._check_user_disconnected("after firmware version")
            _LOGGER.debug(
                "Device identity: serial=%s  firmware=%s",
                self._serial_number,
                self._fw_version,
            )
        except UpdateFailed:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch serial/firmware (will retry after cooldown)")
            self._serial_number = None  # ensure we retry
            raise

        # Read the device's self-describing SFR register directory — an
        # ASCII listing of every register with address, size, type, and
        # signedness.  One-time read; failure is non-fatal (the listing is
        # informational). A failed transfer releases its connection; no
        # partial listing is accepted.
        if self._sfr_file is None:
            try:
                self._sfr_file = await self._client.get_sfr_file()
                self._optional_errors.pop("register_directory", None)
                if self._sfr_file:
                    _LOGGER.info(
                        "Read device SFR register directory: %d bytes, %d entries "
                        "(full listing at debug level and in diagnostics download)",
                        len(self._sfr_file),
                        len(self._sfr_file.splitlines()),
                    )
                    _LOGGER.debug(
                        "Device SFR register directory:\n%s", self._sfr_file
                    )
                else:
                    # Some firmware (observed: RC-103 FW 4.14) returns an
                    # empty SFR_FILE over BLE even though the read succeeds.
                    _LOGGER.debug(
                        "Device returned an empty SFR register directory "
                        "(not provided over BLE on this firmware)"
                    )
            except Exception as err:  # noqa: BLE001
                self._optional_errors["register_directory"] = str(err)
                _LOGGER.debug("SFR directory read failed (non-fatal): %s", err)
        self._check_user_disconnected("after register directory")

        # Push the serial number and firmware version into the HA device
        # registry so they appear on the device info card.
        registry = dr.async_get(self.hass)
        if hasattr(registry, "async_get_device_by_identifier"):
            device = registry.async_get_device_by_identifier(
                (DOMAIN, self._address), self._entry_id
            )
        else:
            # Compatibility with HA versions predating the 2026.8 registry API.
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
        if self._stopping:
            raise UpdateFailed("Radiacode integration is shutting down")

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
                self._remember_connection_source()
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
            if isinstance(first_err, RadiaCodeInitError):
                _LOGGER.warning(
                    "RadiaCode init step %r failed: %s — retrying once",
                    first_err.step, first_err.__cause__,
                )
            else:
                _LOGGER.debug(
                    "Poll failed (was_connected=%s): %s — disconnecting and retrying",
                    was_connected, first_err,
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
            self._remember_connection_source()
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
            if isinstance(retry_err, RadiaCodeInitError):
                # Surface the failing init step so the user can see it on
                # the BLE Connected sensor without enabling debug logs.
                self._last_error = (
                    f"RadiaCode init step '{retry_err.step}' failed for "
                    f"{self._address}: {retry_err.__cause__}"
                )
            else:
                self._last_error = (
                    f"Error communicating with RadiaCode {self._address} "
                    f"(retry also failed): {retry_err}"
                )
            raise UpdateFailed(self._last_error) from retry_err

    async def async_write_setting(self, vsfr_id: int, value: int) -> None:
        """Write a single device setting register and refresh data.

        Raises UpdateFailed if the write fails or the device rejects the value.
        """
        self._check_user_disconnected("write setting")
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
        self._next_settings_read = 0.0
        await self.async_request_refresh()

    async def async_reset_dose(self) -> None:
        """Reset the accumulated dose counter on the device.

        Also clears the locally cached accumulated dose so the sensor drops
        to zero immediately instead of showing the stale pre-reset value
        until the next RareData record arrives (~1 minute later).
        """
        self._check_user_disconnected("reset dose")
        if not self._client.is_connected:
            raise UpdateFailed("Cannot reset dose: device not connected")

        try:
            ok = await self._client.write_vsfr(VSFR.DOSE_RESET, 1)
        except Exception as err:
            raise UpdateFailed(f"Failed to reset accumulated dose: {err}") from err

        if not ok:
            raise UpdateFailed("Device rejected the accumulated dose reset")

        self._last_accumulated_dose = 0.0
        if self.data is not None:
            self.data = replace(
                self.data, sensors=replace(self.data.sensors, accumulated_dose=0.0)
            )
            self.async_update_listeners()
        await self.async_request_refresh()

    async def async_reset_spectrum(self) -> None:
        """Reset the current spectrum accumulation on the device.

        Clears the cached snapshot and schedules a fresh spectrum read on
        the next poll so the sensor reflects the reset promptly.
        """
        self._check_user_disconnected("reset spectrum")
        if not self._client.is_connected:
            raise UpdateFailed("Cannot reset spectrum: device not connected")

        try:
            ok = await self._client.reset_spectrum()
        except Exception as err:
            raise UpdateFailed(f"Failed to reset spectrum: {err}") from err

        if not ok:
            raise UpdateFailed("Device rejected the spectrum reset")

        # This explicit reset requests a fresh histogram. Permit another
        # bounded series of automatic attempts, including when previously
        # paused, so the reset does not strand the sensor at unknown.
        self._spectrum_consecutive_failures = 0
        self._automatic_spectrum_paused = False
        self._spectrum_retry_delay = max(_SPECTRUM_RETRY_MIN, self._spectrum_interval)
        self._last_spectrum = None
        self._last_spectrum_error = None
        self._last_spectrum_success = None
        self._optional_errors.pop("spectrum", None)
        self._next_spectrum_read = 0.0
        self._publish_maintenance_cache()
        await self.async_request_refresh()

    async def async_get_spectrum(self, accumulated: bool = False) -> Spectrum:
        """Fetch a spectrum on demand (service call).

        Raises UpdateFailed when the device is not connected or the read
        fails; the caller converts this into a service error.
        """
        self._check_user_disconnected("read spectrum")
        if not self._client.is_connected:
            raise UpdateFailed("Cannot read spectrum: device not connected")
        started = time.monotonic()
        error = None
        phase = "accumulated_spectrum" if accumulated else "spectrum"
        if not accumulated:
            self._last_spectrum_attempt = started
        try:
            spectrum = await self._client.get_spectrum(accumulated=accumulated)
            self._check_user_disconnected("after requested spectrum")
            self._optional_errors.pop(phase, None)
            if not accumulated:
                self._record_spectrum_success(spectrum)
                self._publish_maintenance_cache()
            return spectrum
        except Exception as err:
            error = str(err)
            self._optional_errors[phase] = error
            if not accumulated:
                self._record_spectrum_failure(error)
            raise UpdateFailed(f"Spectrum read failed: {err}") from err
        finally:
            self._record_phase(f"{phase}_action", started, error)
