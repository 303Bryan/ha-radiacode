"""
RadiaCode BLE client — async I/O layer built on bleak.

Usage pattern (persistent connection across polls):

    client = RadiaCodeBLEClient()
    await client.connect(ble_device)
    data = await client.get_data()   # RadiaCodeData dataclass
    # ... next poll ...
    data = await client.get_data()
    await client.disconnect()

The device requires an initialization sequence on every connection
(SET_EXCHANGE handshake + SET_TIME + DEVICE_TIME write). This is
handled automatically by connect().

BLE notification reassembly
────────────────────────────
Responses arrive in one or more BLE notify packets. The first packet
carries a 4-byte signed length prefix; subsequent packets are
continuations. We accumulate packets into _resp_buf until the
declared number of bytes is received.  _notify_event signals progress or a
connection drop; no partial frame is returned as a successful reply.

Command sequencing
──────────────────
Commands are strictly sequential (one in-flight at a time). The
seq counter (0–31) is encoded in each command and echoed by the
device, letting parse_response_body() detect mismatched replies.

Write mode
──────────
Writes use response=False (ATT Write Command / Write Without Response).
Through ESPHome BT proxies, ATT Write Requests (response=True) can
hang for 10+ seconds waiting for a Write Response that never arrives
through the proxy relay, even though the device processes the command
and sends notification data back.  Write Without Response is fire-and-
forget; we confirm the command was processed via notification replies.

Connection resilience
─────────────────────
Reconnecting after a dropped BLE link requires careful teardown of
the old BleakClient:
  • Callbacks are scoped to their originating client to ignore stale events
  • Incomplete or cancelled exchanges retire the connection before reuse
  • Notification reassembly state (_resp_buf, _resp_total) is reset
  • _client is set to None *before* the slow disconnect() call so
    is_connected returns False immediately, avoiding rapid retry loops
  • A disconnect callback is registered on BleakClient to detect
    connection drops immediately and unblock any waiting _execute()
"""

import asyncio
import datetime
import logging
import struct
from collections import Counter, deque
from copy import deepcopy
from contextlib import asynccontextmanager
from typing import Optional

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from .protocol import (
    CMD,
    VS,
    VSFR,
    DIAGNOSTIC_VSFR_IDS,
    SETTINGS_VSFR_IDS,
    SERVICE_UUID,
    WRITE_CHAR_UUID,
    NOTIFY_CHAR_UUID,
    RadiaCodeData,
    RadiaCodeDiagnostics,
    RadiaCodeSettings,
    Spectrum,
    build_command,
    decode_spectrum,
    detect_spectrum_format,
    parse_spec_format_version,
    parse_response_body,
    parse_vs_response,
    parse_vsfr_batch_response,
    parse_vsfr_read_response,
    parse_write_response,
    decode_data_buf,
    decode_diagnostics,
    decode_settings,
    decode_sfr_file,
    extract_sensor_values,
    decode_serial_number,
    parse_firmware_version,
)


class RadiaCodeInitError(Exception):
    """Raised when the post-connect initialisation sequence fails.

    Carries the *step* name (e.g. ``"set_exchange"``) so the coordinator
    can surface which part of init failed to the user.  The original
    exception is chained via ``__cause__`` for log-level diagnostics.
    """

    def __init__(self, step: str, cause: BaseException) -> None:
        super().__init__(f"RadiaCode init step {step!r} failed: {cause}")
        self.step = step


_LOGGER = logging.getLogger(__name__)

# Maximum bytes per write (BLE MTU constraint; both cdump and mkgeiger use 18)
_WRITE_CHUNK = 18

# One deadline covers all command writes and response notifications.
# Failed transports are then disconnected with a separate bounded cleanup.
_CMD_TIMEOUT = 10.0

# Once a bulk response starts, allow time for thousands of bytes to arrive.
# No reply or a blocked write still uses the ordinary deadline; a response
# that stops progressing still uses the same inter-packet stall deadline.
_BULK_CMD_TIMEOUT = 30.0
_BULK_VIRTUAL_STRINGS = frozenset({
    VS.SPECTRUM, VS.SPEC_ACCUM, VS.CONFIGURATION, VS.SFR_FILE,
})

# A response that stops mid-frame cannot safely be followed by another
# command: its late continuations have no framing header of their own.
# Retire the connection after this much silence instead of returning a prefix.
_STALL_TIMEOUT = 2.0

_DISCONNECT_TIMEOUT = 5.0
_TEMPERATURE_INTERVAL = 60.0
_COMMAND_HISTORY_LIMIT = 20

# establish_connection() timeout per attempt.  15 s is generous for a BT
# proxy hop; if the ESP32 can't connect in this window the slot is likely
# stuck and we should fail fast so the coordinator can retry cleanly.
_CONNECT_TIMEOUT = 15.0


class RadiaCodeBLEClient:
    """
    Async BLE client for RadiaCode radiation detectors (RC-101/102/103/110).

    Replicates the transport behaviour of cdump/radiacode's Bluetooth class
    using bleak instead of bluepy, making it compatible with HA's Bluetooth
    proxy infrastructure.
    """

    def __init__(self) -> None:
        self._client: Optional[BleakClient] = None
        # External disconnect invalidates reconnect work already queued or
        # awaiting backend cleanup/establishment.  Internal cleanup does not.
        self._disconnect_generation: int = 0
        self._connection_generation: int = 0
        self._seq: int = 0
        self._base_time: Optional[datetime.datetime] = None

        # Notification reassembly state
        self._resp_buf: bytearray = bytearray()
        # _resp_total tracks the total frame bytes still expected:
        #   0 = idle or complete, >0 = bytes remaining
        self._resp_total: int = 0
        self._notify_event: asyncio.Event = asyncio.Event()
        self._response_started: bool = False
        self._response_error: Optional[Exception] = None
        self._last_notification: Optional[float] = None
        self._command_stats: Optional[dict] = None
        self._command_history: deque[dict] = deque(maxlen=_COMMAND_HISTORY_LIMIT)
        self._transport_metadata: dict = {}
        self._initialization_steps: list[dict] = []
        self._last_disconnect_reason: Optional[str] = None

        # Guard: _on_notify ignores packets arriving when no command is in-flight.
        self._expecting_response: bool = False

        # Set by the BleakClient disconnect callback to unblock _execute()
        # immediately when the BLE link drops.
        self._disconnected_event: asyncio.Event = asyncio.Event()

        # Serialize BLE command execution.  Without this, a UI-triggered
        # write (e.g. switch toggle) can overlap with a coordinator poll,
        # corrupting the shared notification reassembly state.
        self._cmd_lock: asyncio.Lock = asyncio.Lock()
        self._command_lock_wait_s: float = 0.0

        # A uniquely valid complete spectrum identifies its own encoding.
        # Keep that observation across reconnects and revalidate every payload.
        # Only an explicit configuration read may resolve an ambiguous payload.
        self._spectrum_format_version: int = 0
        self._spectrum_format_loaded: bool = False
        self._spectrum_format_authoritative: bool = False
        self._spectrum_format_source: Optional[str] = None
        self._firmware_version: Optional[str] = None

        # Temperature changes slowly; DATA_BUF also carries RareData samples.
        # Avoid a separate register command on every regular sensor poll.
        self._temperature: Optional[float] = None
        self._last_temperature_read: Optional[float] = None

    # ── Connection management ─────────────────────────────────────────────────

    def _reset_notification_state(self) -> None:
        """Reset state for a new connection while holding the command lock."""
        self._resp_buf = bytearray()
        self._resp_total = 0
        self._notify_event.clear()
        self._response_started = False
        self._response_error = None
        self._last_notification = None
        self._expecting_response = False
        self._disconnected_event.clear()

    async def connect(self, ble_device: BLEDevice) -> None:
        """
        Connect to a RadiaCode device and run the required init sequence.

        If a previous BleakClient exists (stale connection), it is torn down
        first.  Notifications from retired clients are ignored, including
        callbacks already queued before the old transport disconnected.

        The init sequence (SET_EXCHANGE → SET_TIME → DEVICE_TIME=0) must be
        completed before the device streams data_buf records.

        Raises:
            Exception: if bleak_retry_connector cannot establish a connection.
            RadiaCodeInitError: if the post-connect init sequence fails.
                ``step`` identifies which sub-step failed so the coordinator
                can surface it to the user (issue #9: early RC-101 hardware).
        """
        # Keep reconnect/init separate from controls and polls.  Init calls
        # the locked command helper because this lock is already held.
        generation = self._disconnect_generation
        async with self._command_lock_context():
            self._check_connect_generation(generation)
            try:
                await self._connect_locked(ble_device, generation)
            except BaseException as err:
                # Failed or cancelled init must release the peripheral too.
                await self._retire_current_client(f"connection_init:{type(err).__name__}")
                raise

    def _check_connect_generation(self, generation: int) -> None:
        """Abort connection work superseded by an explicit disconnect."""
        if generation != self._disconnect_generation:
            raise ConnectionError("BLE connection cancelled by disconnect")

    async def _connect_locked(
        self, ble_device: BLEDevice, generation: int
    ) -> None:
        """Connect and initialise while holding the command lock."""
        await self._retire_current_client("reconnect")
        self._check_connect_generation(generation)

        self._seq = 0
        self._reset_notification_state()
        self._connection_generation += 1
        self._initialization_steps = []
        # Preserve the optional temperature deadline across reconnects.
        # Repeated failed temperature reads must not force every poll to
        # disconnect/reinitialise an otherwise usable radiation data stream.

        # Pass the disconnected callback via establish_connection.
        # ``BleakClient.set_disconnected_callback`` was deprecated in bleak
        # 0.18 and removed in bleak 1.0; calling it on a recent install
        # raises AttributeError before the init sequence ever runs.
        self._client = await self._run_init_step(
            "establish_connection", establish_connection(
                BleakClient,
                ble_device,
                ble_device.address,
                disconnected_callback=self._on_ble_disconnect,
                max_attempts=2,
                timeout=_CONNECT_TIMEOUT,
            ),
        )
        self._check_connect_generation(generation)

        # ── Verify the device exposes the expected RadiaCode service ────────
        # Early RC-101 hardware (~2019) sometimes connects but doesn't expose
        # the e63215e5-… service.  Surface that with a clear error rather
        # than a TimeoutError on the first SET_EXCHANGE attempt.
        services = self._client.services
        service_uuids = [s.uuid for s in services]
        if SERVICE_UUID not in service_uuids:
            _LOGGER.warning(
                "RadiaCode service %s not found on %s. Discovered services: %s",
                SERVICE_UUID, ble_device.address, service_uuids,
            )
            raise RadiaCodeInitError(
                "service_discovery",
                RuntimeError(
                    f"RadiaCode GATT service {SERVICE_UUID} not advertised; "
                    f"discovered services: {service_uuids or 'none'}"
                ),
            )

        mtu = getattr(self._client, "mtu_size", None)
        self._transport_metadata = {
            "backend": f"{type(self._client).__module__}.{type(self._client).__name__}",
            "mtu_size": mtu if isinstance(mtu, int) else None,
            "service_count": len(service_uuids),
            "active_connection_source": None,
        }
        # BLEDevice.details is public, but its contents vary by backend.
        # Record only known scalar source fields, never arbitrary backend state.
        details = getattr(ble_device, "details", None)
        if isinstance(details, dict):
            for key in ("source", "source_name", "scanner_source"):
                value = details.get(key)
                if isinstance(value, str):
                    self._transport_metadata[f"connection_request_{key}"] = value[:120]
        _LOGGER.debug(
            "RadiaCode BLE connected: generation=%d transport=%s",
            self._connection_generation, self._transport_metadata,
        )

        client = self._client
        try:
            # Bind the originating client into the callback.  A callback
            # already queued by a retired transport must not touch new state.
            await self._run_init_step("start_notify", asyncio.wait_for(
                client.start_notify(
                    NOTIFY_CHAR_UUID,
                    lambda sender, data: self._on_client_notify(client, sender, data),
                ),
                timeout=_CMD_TIMEOUT,
            ))
        except Exception as err:
            raise RadiaCodeInitError("start_notify", err) from err
        self._check_connect_generation(generation)

        # ── Init sequence (mirrors cdump RadiaCode.__init__) ──────────────────
        # Each step is wrapped so the coordinator can surface which part of
        # init failed (e.g. "RC-101 didn't reply to SET_EXCHANGE").
        # 1. Handshake — device expects this exact payload before responding to data
        try:
            await self._run_init_step("set_exchange", self._execute_locked(CMD.SET_EXCHANGE, b"\x01\xff\x12\xff"))
        except Exception as err:
            raise RadiaCodeInitError("set_exchange", err) from err

        # 2. Sync device clock to host time
        now = datetime.datetime.now()
        time_payload = struct.pack(
            "<BBBBBBBB",
            now.day, now.month, now.year - 2000, 0,
            now.second, now.minute, now.hour, 0,
        )
        try:
            await self._run_init_step("set_time", self._execute_locked(CMD.SET_TIME, time_payload))
        except Exception as err:
            raise RadiaCodeInitError("set_time", err) from err

        # 3. Zero out DEVICE_TIME VSFR
        try:
            await self._run_init_step("device_time", self._execute_locked(
                CMD.WR_VIRT_SFR,
                struct.pack("<II", int(VSFR.DEVICE_TIME), 0),
            ))
        except Exception as err:
            raise RadiaCodeInitError("device_time", err) from err

        # base_time anchors the 10 ms timestamp offsets inside data_buf records.
        # cdump sets this to now+128 s during init.
        self._base_time = datetime.datetime.now() + datetime.timedelta(seconds=128)

        # Drain stale records accumulated while disconnected.  The full
        # framed response must arrive before another command can be sent.
        try:
            await self._run_init_step("data_buf", self._read_vs_locked(VS.DATA_BUF))
            _LOGGER.debug("Drained stale data_buf after init")
        except Exception as err:
            raise RadiaCodeInitError("data_buf", err) from err

        _LOGGER.debug(
            "RadiaCode connected and initialised (%s)", ble_device.address
        )

    async def _run_init_step(self, step: str, operation):
        """Measure each initialization operation without recording payloads."""
        started = asyncio.get_running_loop().time()
        outcome = "success"
        try:
            return await operation
        except BaseException as err:
            outcome = type(err).__name__
            raise
        finally:
            elapsed = asyncio.get_running_loop().time() - started
            self._initialization_steps.append({
                "step": step, "elapsed_s": round(elapsed, 4), "outcome": outcome,
            })
            _LOGGER.debug("BLE initialization step=%s elapsed=%.3fs outcome=%s", step, elapsed, outcome)

    async def disconnect(self) -> None:
        """Retire the connection immediately and release its BLE resources.

        Wake an in-flight command before waiting for transport cleanup.
        Notification callbacks from this client are ignored once retired.
        Bleak automatically stops notifications when disconnecting.
        """
        self._disconnect_generation += 1
        await self._retire_current_client("user_disconnect")

    async def _retire_current_client(self, reason: str = "retired") -> None:
        """Clean up a transport without invalidating a queued reconnect."""
        client = self._client
        self._client = None
        self._expecting_response = False
        self._disconnected_event.set()
        self._notify_event.set()

        if client is None:
            return

        self._last_disconnect_reason = reason
        _LOGGER.debug("Retiring BLE connection generation=%d reason=%s", self._connection_generation, reason)

        # A second cancellation (e.g. unload during cancelled maintenance)
        # must not cancel backend cleanup and strand a proxy connection slot.
        # Hold the command lock until bounded cleanup finishes, then preserve
        # cancellation so a queued reconnect cannot race the old transport.
        cleanup = asyncio.create_task(self._disconnect_client(client))
        cancellation = None
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError as err:
                if cleanup.cancelled():
                    raise
                cancellation = err
        if cancellation is not None:
            raise cancellation

    async def _disconnect_client(self, client: BleakClient) -> None:
        """Bound physical cleanup independently of caller cancellation."""
        try:
            await asyncio.wait_for(client.disconnect(), timeout=_DISCONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.debug("Disconnect timed out — client retired")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Ignored error during disconnect: %s", err)

    @property
    def is_connected(self) -> bool:
        return (
            self._client is not None
            and self._client.is_connected
            and not self._disconnected_event.is_set()
        )

    @property
    def spectrum_format_version(self) -> Optional[int]:
        """Return the observed/configured format, or None before discovery."""
        return self._spectrum_format_version if self._spectrum_format_loaded else None

    @property
    def spectrum_format_source(self) -> Optional[str]:
        """Return how the current spectrum encoding was established."""
        return self._spectrum_format_source

    @property
    def transport_diagnostics(self) -> dict:
        """Return bounded command summaries suitable for HA diagnostics."""
        return {
            "connection_generation": self._connection_generation,
            "transport": dict(self._transport_metadata),
            "last_disconnect_reason": self._last_disconnect_reason,
            "initialization_steps": [dict(step) for step in self._initialization_steps],
            "recent_commands": deepcopy(list(self._command_history)),
            "spectrum_format_source": self._spectrum_format_source,
        }

    def _on_ble_disconnect(self, client: BleakClient) -> None:
        """Wake the current command when its transport disconnects."""
        if client is not self._client:
            return
        self._last_disconnect_reason = "remote_disconnect"
        _LOGGER.debug("BLE disconnect callback generation=%d", self._connection_generation)
        self._disconnected_event.set()
        self._notify_event.set()

    # ── High-level API ────────────────────────────────────────────────────────

    async def get_data(self) -> RadiaCodeData:
        """
        Poll the device and return the latest sensor readings.

        Data sources:
          - data_buf records: dose_rate (from DoseRateDB/RawData/RealTimeData),
            count_rate (from RealTimeData), accumulated_dose and battery
            (from RareData, ~once per minute).

        Returns a RadiaCodeData with:
          dose_rate        – from data_buf (DoseRateDB, RawData, or RealTimeData)
          count_rate       – CPS (from data_buf RealTimeData)
          accumulated_dose – µSv (from data_buf RareData)
          battery          – % (from data_buf RareData, None most polls)
          temperature      – °C (new RareData sample, otherwise None)
        """
        # This is the sole operation on the primary reading path. Publish its
        # result before optional maintenance commands can stall or disconnect.
        raw = await self._read_vs(VS.DATA_BUF)
        records = decode_data_buf(raw, self._base_time)
        buf_data = extract_sensor_values(records)
        now = asyncio.get_running_loop().time()
        if buf_data.temperature is not None:
            self._temperature = buf_data.temperature
            self._last_temperature_read = now
        # Preserve provenance: a cached register temperature is not a new
        # RareData sample. The coordinator owns the displayed-value cache.
        result = buf_data
        measurement_lag = (
            (datetime.datetime.now() - result.measurement_time).total_seconds()
            if result.measurement_time is not None else None
        )
        _LOGGER.debug(
            "DATA_BUF records=%d types=%s measurement_time=%s type=%s "
            "flags=%s lag_s=%s bytes=%d",
            len(records), dict(Counter(type(record).__name__ for record in records)),
            result.measurement_time.isoformat() if result.measurement_time else None,
            result.measurement_type, result.measurement_flags,
            round(measurement_lag, 3) if measurement_lag is not None else None,
            len(raw),
        )

        _LOGGER.debug(
            "get_data → dose_rate=%s µSv/h  count_rate=%.1f CPS  "
            "dose=%s µSv  battery=%s%%  temp=%s°C",
            f"{result.dose_rate:.6e}" if result.dose_rate is not None else "None",
            result.count_rate or 0,
            f"{result.accumulated_dose:.4f}" if result.accumulated_dose is not None else "None",
            f"{result.battery:.0f}" if result.battery is not None else "—",
            f"{result.temperature:.1f}" if result.temperature is not None else "—",
        )
        return result

    async def get_temperature(self) -> Optional[float]:
        """Refresh optional temperature independently of primary readings.

        RareData and successful register values share the same cache. Failed
        attempts keep the 60 second deadline across reconnects; their transport
        errors propagate so maintenance can stop using the retired connection.
        """
        now = asyncio.get_running_loop().time()
        if (
            self._last_temperature_read is not None
            and now - self._last_temperature_read < _TEMPERATURE_INTERVAL
        ):
            return self._temperature
        self._last_temperature_read = now
        values = await self._read_vsfr_batch([VSFR.TEMP_degC])
        if values[0] is not None:
            self._temperature = values[0]
        return self._temperature

    async def get_serial_number(self) -> str:
        """Return the device serial number string, e.g. 'RC-103-012345'."""
        raw = await self._read_vs(VS.SERIAL_NUMBER)
        return decode_serial_number(raw)

    async def get_firmware_version(self) -> str:
        """Return the firmware version string, e.g. '4.8'."""
        payload = await self._execute(CMD.GET_VERSION)
        firmware = parse_firmware_version(payload)
        if firmware != "unknown" and self._firmware_version is not None and firmware != self._firmware_version:
            _LOGGER.debug("Firmware changed %s -> %s; clearing cached spectrum encoding", self._firmware_version, firmware)
            self._spectrum_format_loaded = False
            self._spectrum_format_authoritative = False
            self._spectrum_format_source = None
        if firmware != "unknown":
            self._firmware_version = firmware
        return firmware

    async def get_settings(self) -> RadiaCodeSettings:
        """Read all device settings via a single VSFR batch read.

        Returns a RadiaCodeSettings with current display, sound, vibration,
        and alarm threshold values.  This is a small response compared
        with the spectrum and configuration transfers.
        """
        values = await self._read_vsfr_batch(SETTINGS_VSFR_IDS)
        return decode_settings(values)

    async def get_configuration(self) -> str:
        """Read the device configuration text (cp1251-encoded)."""
        raw = await self._read_vs(VS.CONFIGURATION)
        return raw.decode("cp1251", errors="replace")

    async def refresh_spectrum_format(self) -> None:
        """Read and validate the spectrum format from device configuration.

        Transport errors propagate: a partial configuration response cannot
        establish the format or safely precede another BLE command.
        """
        async with self._command_lock_context():
            await self._refresh_spectrum_format_locked()

    async def _refresh_spectrum_format_locked(self) -> None:
        """Discover the format while holding the command lock."""
        raw = await self._read_vs_locked(VS.CONFIGURATION)
        config = raw.decode("cp1251", errors="replace")
        declared_values = [
            value.strip()
            for line in config.splitlines()
            for key, separator, value in [line.partition("=")]
            if separator and key.strip() == "SpecFormatVersion"
        ]
        version = parse_spec_format_version(config)
        try:
            if declared_values:
                versions = [int(value) for value in declared_values]
                if any(value not in (0, 1) for value in versions) or len(set(versions)) != 1:
                    raise ValueError("Unsupported or conflicting declared spectrum format")
                version = versions[0]
        except ValueError as err:
            error = ValueError("Invalid RadiaCode SpecFormatVersion declaration")
            self._record_payload_error(error, raw)
            raise error from err
        self._spectrum_format_version = version
        self._spectrum_format_loaded = True
        self._spectrum_format_authoritative = bool(declared_values)
        self._spectrum_format_source = (
            "configuration" if declared_values else "configuration_default"
        )
        _LOGGER.debug(
            "Spectrum format version: %d", self._spectrum_format_version
        )

    async def get_spectrum(self, accumulated: bool = False) -> Spectrum:
        """Read a complete, 1024-channel gamma spectrum.

        Detect the encoding directly from a complete, uniquely valid payload.
        A multi-kilobyte configuration transfer is never a prerequisite. A
        cached authoritative configuration may resolve an ambiguous payload;
        an observed encoding alone is not sufficient evidence in that case.
        """
        async with self._command_lock_context():
            vs_id = VS.SPEC_ACCUM if accumulated else VS.SPECTRUM
            raw = await self._read_vs_locked(vs_id)
            try:
                version, spectrum = detect_spectrum_format(raw)
            except ValueError as err:
                if not (
                    str(err).startswith("Ambiguous spectrum encoding")
                    and self._spectrum_format_authoritative
                ):
                    self._record_payload_error(err, raw)
                    raise
                version = self._spectrum_format_version
                spectrum = decode_spectrum(raw, version)
            if spectrum.truncated or len(spectrum.counts) != 1024:
                raise ValueError(
                    "Incomplete RadiaCode spectrum: "
                    f"received {len(spectrum.counts)} of 1024 channels"
                )
            if self._spectrum_format_loaded and version != self._spectrum_format_version:
                self._spectrum_format_authoritative = False
                _LOGGER.debug("Complete spectrum encoding changed %d -> %d", self._spectrum_format_version, version)
            self._spectrum_format_version = version
            self._spectrum_format_loaded = True
            if not self._spectrum_format_authoritative:
                self._spectrum_format_source = "complete_payload"
            _LOGGER.debug("Spectrum target=%s format=%d bytes=%d channels=%d duration=%ds", vs_id.name, version, len(raw), len(spectrum.counts), spectrum.duration_s)
            return spectrum

    async def reset_spectrum(self) -> bool:
        """Reset the current spectrum accumulation.  Returns True on success."""
        payload = await self._execute(
            CMD.WR_VIRT_STRING, struct.pack("<II", int(VS.SPECTRUM), 0)
        )
        return parse_write_response(payload)

    async def get_sfr_file(self) -> str:
        """Read the device's self-describing SFR register directory.

        Returns an ASCII listing of every Special Function Register the
        firmware supports — address, size, type, and signedness.  The
        listing can be several KB.  Incomplete transfers raise an error
        and retire the connection rather than returning a partial listing.
        """
        raw = await self._read_vs(VS.SFR_FILE)
        return decode_sfr_file(raw)

    async def get_diagnostics(self) -> RadiaCodeDiagnostics:
        """Read device-health registers via a single VSFR batch read.

        Returns a RadiaCodeDiagnostics with SiPM bias voltage, MCU
        temperature/Vref, and accelerometer axes.  Registers the device
        rejects over BLE come back as None fields.
        """
        values = await self._read_vsfr_batch(DIAGNOSTIC_VSFR_IDS)
        return decode_diagnostics(values)

    async def write_vsfr(self, vsfr_id: int, value: int) -> bool:
        """Write a single VSFR register.  Returns True on success.

        The *value* is always packed as uint32.  For bool registers pass 1/0;
        for byte registers (e.g. brightness 0-9) pass the integer directly.
        """
        args = struct.pack("<II", vsfr_id, value)
        payload = await self._execute(CMD.WR_VIRT_SFR, args)
        return parse_write_response(payload)

    # ── Notification handler ──────────────────────────────────────────────────

    def _on_client_notify(
        self, client: BleakClient, sender: object, data: bytearray
    ) -> None:
        """Ignore callbacks queued by a transport that has been retired."""
        if client is self._client:
            self._on_notify(sender, data)

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        """Assemble one strictly framed reply and wake the command on progress.

        The first packet starts with an int32 little-endian body length;
        continuation packets carry only body bytes.  Reject malformed
        lengths and overflows instead of treating them as complete frames.
        """
        if not self._expecting_response or self._response_error is not None:
            return
        if self._response_started and self._resp_total == 0:
            return

        now = asyncio.get_running_loop().time()
        stats = self._command_stats
        if stats is not None:
            stats["notification_count"] += 1
            sizes = stats["notification_sizes"]
            size_key = str(len(data))
            if size_key not in sizes and len(sizes) >= 32:
                size_key = "other"
            sizes[size_key] = sizes.get(size_key, 0) + 1
            if stats["first_byte_s"] is None and data:
                stats["first_byte_s"] = round(now - stats["_started"], 4)
            if self._last_notification is not None:
                stats["max_notification_gap_s"] = max(
                    stats["max_notification_gap_s"], now - self._last_notification,
                )

        if not self._response_started:
            if len(data) < 4:
                self._response_error = ValueError(
                    f"BLE response length header too short: {len(data)} bytes"
                )
            else:
                (body_len,) = struct.unpack_from("<i", data, 0)
                if body_len < 4:
                    self._response_error = ValueError(
                        f"Invalid BLE response body length: {body_len}"
                    )
                else:
                    self._response_started = True
                    if stats is not None:
                        stats["declared_body_bytes"] = body_len
                    self._resp_total = body_len
                    self._resp_buf.extend(data[4:])
                    self._resp_total -= len(data) - 4
        else:
            self._resp_buf.extend(data)
            self._resp_total -= len(data)

        if self._resp_total < 0:
            self._response_error = ValueError(
                f"BLE response overflow by {-self._resp_total} bytes"
            )
        self._last_notification = now
        self._notify_event.set()

    # ── Low-level command execution ───────────────────────────────────────────

    @asynccontextmanager
    async def _command_lock_context(self):
        """Measure queue delay independently of writes and device response.

        Compound operations hold the lock across multiple commands. Attribute
        their queue delay to the first command, then subsequent commands have
        zero lock wait because the operation already owns the lock.
        """
        started = asyncio.get_running_loop().time()
        async with self._cmd_lock:
            self._command_lock_wait_s = asyncio.get_running_loop().time() - started
            try:
                yield
            finally:
                self._command_lock_wait_s = 0.0

    async def _execute(self, cmd: int, args: bytes = b"") -> bytes:
        """Serialize commands, then return one complete validated reply."""
        async with self._command_lock_context():
            return await self._execute_locked(cmd, args)

    async def _execute_locked(self, cmd: int, args: bytes = b"") -> bytes:
        """Run one command while holding the lock, retiring unsafe transports.

        Write failures, cancellations and incomplete responses leave the
        stream position unknown.  Disconnect before the lock is released so
        a late continuation cannot be interpreted as another frame header.
        """
        client = self._client
        if client is None or not self.is_connected:
            raise ConnectionError(
                f"Not connected — cannot send command {cmd:#06x}"
            )

        seq = self._seq
        self._seq = (self._seq + 1) % 32
        packet = build_command(cmd, seq, args)
        self._resp_buf = bytearray()
        self._resp_total = 0
        self._response_started = False
        self._response_error = None
        self._last_notification = None
        self._notify_event.clear()
        self._expecting_response = True
        started = asyncio.get_running_loop().time()
        target = None
        target_id = None
        if cmd in (CMD.RD_VIRT_STRING, CMD.WR_VIRT_STRING) and len(args) >= 4:
            target_id = struct.unpack_from("<I", args)[0]
            try:
                target = VS(target_id).name
            except ValueError:
                target = f"0x{target_id:08x}"
        try:
            command_name = CMD(cmd).name
        except ValueError:
            command_name = f"0x{cmd:04x}"
        stats = {
            "command": f"0x{cmd:04x}", "command_name": command_name,
            "virtual_string": target, "sequence": seq,
            "connection_generation": self._connection_generation,
            "lock_wait_s": round(self._command_lock_wait_s, 4),
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "request_bytes": len(packet), "completed_write_chunks": 0,
            "notification_count": 0, "notification_sizes": {},
            "declared_body_bytes": None, "received_body_bytes": 0,
            "missing_body_bytes": None, "first_byte_s": None,
            "max_notification_gap_s": 0.0, "deadline_s": _CMD_TIMEOUT,
            "outcome": "success",
            "error_category": None, "_started": started,
        }
        self._command_lock_wait_s = 0.0
        self._command_stats = stats
        phase = "write"
        ended = None
        bulk_deadline_extended = False
        bulk_transfer = (
            cmd == CMD.RD_VIRT_STRING and target_id in _BULK_VIRTUAL_STRINGS
        )

        try:
            # This includes writes: even a Write Without Response operation
            # can stall in the host/proxy backend before it queues the bytes.
            async with asyncio.timeout(_CMD_TIMEOUT) as command_timeout:
                for offset in range(0, len(packet), _WRITE_CHUNK):
                    if self._disconnected_event.is_set() or self._client is not client:
                        raise ConnectionError(
                            f"BLE disconnected during command {cmd:#06x} (seq={seq})"
                        )
                    await client.write_gatt_char(
                        WRITE_CHAR_UUID,
                        packet[offset: offset + _WRITE_CHUNK],
                        response=False,
                    )
                    stats["completed_write_chunks"] += 1

                phase = "response"
                while True:
                    if self._disconnected_event.is_set() or self._client is not client:
                        raise ConnectionError(
                            f"BLE connection lost during command {cmd:#06x} (seq={seq})"
                        )
                    if self._response_error is not None:
                        raise self._response_error
                    if (
                        bulk_transfer and self._response_started
                        and not bulk_deadline_extended
                    ):
                        command_timeout.reschedule(started + _BULK_CMD_TIMEOUT)
                        stats["deadline_s"] = _BULK_CMD_TIMEOUT
                        bulk_deadline_extended = True
                    if self._response_started and self._resp_total == 0:
                        phase = "validate_echo"
                        return parse_response_body(bytes(self._resp_buf), cmd, seq)

                    self._notify_event.clear()
                    if self._last_notification is None:
                        await self._notify_event.wait()
                    else:
                        stall_remaining = (
                            self._last_notification + _STALL_TIMEOUT
                            - asyncio.get_running_loop().time()
                        )
                        if stall_remaining > 0:
                            try:
                                await asyncio.wait_for(
                                    self._notify_event.wait(), timeout=stall_remaining
                                )
                                continue
                            except asyncio.TimeoutError:
                                pass
                        raise TimeoutError(
                            f"Incomplete response to RadiaCode command {cmd:#06x} "
                            f"(seq={seq}): received {len(self._resp_buf)} bytes, "
                            f"missing {self._resp_total}"
                        )
        except BaseException as err:
            ended = asyncio.get_running_loop().time()
            if isinstance(err, asyncio.CancelledError):
                category = "cancelled"
            elif isinstance(err, TimeoutError):
                category = (
                    "write_timeout" if phase == "write" else
                    "incomplete_response" if self._response_started else "no_response"
                )
            elif isinstance(err, ConnectionError):
                category = "connection_lost"
            elif isinstance(err, ValueError):
                category = "invalid_echo" if phase == "validate_echo" else "invalid_frame"
            else:
                category = "write_error" if phase == "write" else "transport_error"
            stats["outcome"] = "error"
            stats["error_category"] = category
            stats["error_type"] = type(err).__name__
            if isinstance(err, ValueError) and _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug("BLE protocol failure cmd=%#06x seq=%d body_prefix=%s error=%s", cmd, seq, self._safe_response_preview(self._resp_buf, stats), err)
            # Retiring also wakes waiters immediately and filters ghost
            # callbacks.  If a user disconnect already retired this client,
            # that caller owns its cleanup.
            if self._client is client:
                await self._retire_current_client(f"command:{category}")
            if isinstance(err, TimeoutError) and not str(err):
                raise TimeoutError(
                    f"Timed out during RadiaCode command {cmd:#06x} "
                    f"(seq={seq}): received {len(self._resp_buf)} bytes, "
                    f"missing {self._resp_total}"
                ) from err
            raise
        finally:
            self._expecting_response = False
            ended = ended if ended is not None else asyncio.get_running_loop().time()
            stats.pop("_started")
            stats["elapsed_s"] = round(ended - started, 4)
            stats["received_body_bytes"] = len(self._resp_buf)
            stats["missing_body_bytes"] = self._resp_total if self._response_started else None
            stats["max_notification_gap_s"] = round(stats["max_notification_gap_s"], 4)
            stats["last_notification_gap_s"] = (
                round(ended - self._last_notification, 4)
                if self._last_notification is not None else None
            )
            self._command_history.append(stats)
            self._command_stats = None
            _LOGGER.debug("BLE command summary %s", stats)

    async def _read_vs(self, vs_id: int) -> bytes:
        """Execute a RD_VIRT_STRING command and return the VS data bytes."""
        async with self._command_lock_context():
            return await self._read_vs_locked(vs_id)

    async def _read_vs_locked(self, vs_id: int) -> bytes:
        """Read a complete virtual string while holding the command lock."""
        payload = await self._execute_locked(
            CMD.RD_VIRT_STRING, struct.pack("<I", int(vs_id))
        )
        try:
            return parse_vs_response(payload)
        except ValueError as err:
            self._record_payload_error(err, payload)
            raise

    def _record_payload_error(self, error: ValueError, payload: bytes) -> None:
        """Annotate the last complete exchange when its inner payload is invalid."""
        if self._command_history:
            self._command_history[-1].update({
                "outcome": "error", "error_category": "invalid_payload",
                "error_type": type(error).__name__,
            })
        if _LOGGER.isEnabledFor(logging.DEBUG):
            stats = self._command_history[-1] if self._command_history else {}
            _LOGGER.debug("BLE payload failure bytes=%d prefix=%s error=%s", len(payload), self._safe_response_preview(payload, stats), error)

    @staticmethod
    def _safe_response_preview(payload: bytes | bytearray, stats: dict) -> str:
        """Never expose identity or arbitrary configuration text in previews."""
        if (
            stats.get("virtual_string") in {"SERIAL_NUMBER", "CONFIGURATION", "SFR_FILE"}
            or stats.get("command_name") == "GET_SERIAL"
            or not stats
        ):
            return "<suppressed>"
        return bytes(payload[:32]).hex()

    async def _read_vsfr_batch(self, vsfr_ids: list[int]) -> list[int | float | None]:
        """Read multiple VSFR registers in a single command.

        Returns decoded values in the same order as *vsfr_ids*.  Values
        for registers the device marks as invalid are returned as None.
        The response contains a validity bitmask followed by one value
        for each accepted register.
        """
        args = struct.pack("<I", len(vsfr_ids))
        for vid in vsfr_ids:
            args += struct.pack("<I", int(vid))
        payload = await self._execute(CMD.RD_VIRT_SFR_BATCH, args)
        return parse_vsfr_batch_response(payload, vsfr_ids)

    async def _read_vsfr(self, vsfr_id: int) -> int | float:
        """Read a single VSFR register via RD_VIRT_SFR (0x0824).

        Used as a fallback when the batch read marks a register as
        invalid.  DR_uR_h and DS_uR consistently fail in batch reads
        on current firmware but work fine with individual reads.

        Returns the decoded value (int or float depending on
        ``_VSFR_FORMATS``).  Raises on communication or protocol errors.
        """
        payload = await self._execute(
            CMD.RD_VIRT_SFR, struct.pack("<I", int(vsfr_id))
        )
        return parse_vsfr_read_response(payload, vsfr_id)
