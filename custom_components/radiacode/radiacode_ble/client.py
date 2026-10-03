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

# A response that stops mid-frame cannot safely be followed by another
# command: its late continuations have no framing header of their own.
# Retire the connection after this much silence instead of returning a prefix.
_STALL_TIMEOUT = 2.0

_DISCONNECT_TIMEOUT = 5.0
_TEMPERATURE_INTERVAL = 60.0

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

        # Guard: _on_notify ignores packets arriving when no command is in-flight.
        self._expecting_response: bool = False

        # Set by the BleakClient disconnect callback to unblock _execute()
        # immediately when the BLE link drops.
        self._disconnected_event: asyncio.Event = asyncio.Event()

        # Serialize BLE command execution.  Without this, a UI-triggered
        # write (e.g. switch toggle) can overlap with a coordinator poll,
        # corrupting the shared notification reassembly state.
        self._cmd_lock: asyncio.Lock = asyncio.Lock()

        # Upstream defaults to format 0 when the configuration omits the key.
        # Discover the actual format before the first spectrum read per link.
        self._spectrum_format_version: int = 0
        self._spectrum_format_loaded: bool = False

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
        async with self._cmd_lock:
            self._check_connect_generation(generation)
            try:
                await self._connect_locked(ble_device, generation)
            except BaseException:
                # Failed or cancelled init must release the peripheral too.
                await self._retire_current_client()
                raise

    def _check_connect_generation(self, generation: int) -> None:
        """Abort connection work superseded by an explicit disconnect."""
        if generation != self._disconnect_generation:
            raise ConnectionError("BLE connection cancelled by disconnect")

    async def _connect_locked(
        self, ble_device: BLEDevice, generation: int
    ) -> None:
        """Connect and initialise while holding the command lock."""
        await self._retire_current_client()
        self._check_connect_generation(generation)

        self._seq = 0
        self._reset_notification_state()
        self._spectrum_format_version = 0
        self._spectrum_format_loaded = False
        # Preserve the optional temperature deadline across reconnects.
        # Repeated failed temperature reads must not force every poll to
        # disconnect/reinitialise an otherwise usable radiation data stream.

        # Pass the disconnected callback via establish_connection.
        # ``BleakClient.set_disconnected_callback`` was deprecated in bleak
        # 0.18 and removed in bleak 1.0; calling it on a recent install
        # raises AttributeError before the init sequence ever runs.
        self._client = await establish_connection(
            BleakClient,
            ble_device,
            ble_device.address,
            disconnected_callback=self._on_ble_disconnect,
            max_attempts=2,   # allow one internal retry; coordinator adds another layer
            timeout=_CONNECT_TIMEOUT,
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
        _LOGGER.debug(
            "RadiaCode BLE connected: addr=%s mtu=%s services=%d",
            ble_device.address, mtu, len(service_uuids),
        )

        client = self._client
        try:
            # Bind the originating client into the callback.  A callback
            # already queued by a retired transport must not touch new state.
            await asyncio.wait_for(
                client.start_notify(
                    NOTIFY_CHAR_UUID,
                    lambda sender, data: self._on_client_notify(client, sender, data),
                ),
                timeout=_CMD_TIMEOUT,
            )
        except Exception as err:
            raise RadiaCodeInitError("start_notify", err) from err
        self._check_connect_generation(generation)

        # ── Init sequence (mirrors cdump RadiaCode.__init__) ──────────────────
        # Each step is wrapped so the coordinator can surface which part of
        # init failed (e.g. "RC-101 didn't reply to SET_EXCHANGE").
        # 1. Handshake — device expects this exact payload before responding to data
        try:
            await self._execute_locked(CMD.SET_EXCHANGE, b"\x01\xff\x12\xff")
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
            await self._execute_locked(CMD.SET_TIME, time_payload)
        except Exception as err:
            raise RadiaCodeInitError("set_time", err) from err

        # 3. Zero out DEVICE_TIME VSFR
        try:
            await self._execute_locked(
                CMD.WR_VIRT_SFR,
                struct.pack("<II", int(VSFR.DEVICE_TIME), 0),
            )
        except Exception as err:
            raise RadiaCodeInitError("device_time", err) from err

        # base_time anchors the 10 ms timestamp offsets inside data_buf records.
        # cdump sets this to now+128 s during init.
        self._base_time = datetime.datetime.now() + datetime.timedelta(seconds=128)

        # Drain stale records accumulated while disconnected.  The full
        # framed response must arrive before another command can be sent.
        try:
            await self._read_vs_locked(VS.DATA_BUF)
            _LOGGER.debug("Drained stale data_buf after init")
        except Exception as err:
            raise RadiaCodeInitError("data_buf", err) from err

        _LOGGER.debug(
            "RadiaCode connected and initialised (%s)", ble_device.address
        )

    async def disconnect(self) -> None:
        """Retire the connection immediately and release its BLE resources.

        Wake an in-flight command before waiting for transport cleanup.
        Notification callbacks from this client are ignored once retired.
        Bleak automatically stops notifications when disconnecting.
        """
        self._disconnect_generation += 1
        await self._retire_current_client()

    async def _retire_current_client(self) -> None:
        """Clean up a transport without invalidating a queued reconnect."""
        client = self._client
        self._client = None
        self._expecting_response = False
        self._disconnected_event.set()
        self._notify_event.set()

        if client is None:
            return

        try:
            await asyncio.wait_for(
                client.disconnect(), timeout=_DISCONNECT_TIMEOUT
            )
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
        """Return the discovered format, or None before configuration is read."""
        return self._spectrum_format_version if self._spectrum_format_loaded else None

    def _on_ble_disconnect(self, client: BleakClient) -> None:
        """Wake the current command when its transport disconnects."""
        if client is not self._client:
            return
        _LOGGER.debug("BLE disconnect callback fired")
        self._disconnected_event.set()
        self._notify_event.set()

    # ── High-level API ────────────────────────────────────────────────────────

    async def get_data(self) -> RadiaCodeData:
        """
        Poll the device and return the latest sensor readings.

        Data sources:
          - VSFR batch read: temperature (TEMP_degC is the only sensor
            register that works via batch/individual reads over BLE;
            DR_uR_h and DS_uR are rejected by the device firmware).
          - data_buf records: dose_rate (from DoseRateDB/RawData/RealTimeData),
            count_rate (from RealTimeData), accumulated_dose and battery
            (from RareData, ~once per minute).

        Returns a RadiaCodeData with:
          dose_rate        – from data_buf (DoseRateDB, RawData, or RealTimeData)
          count_rate       – CPS (from data_buf RealTimeData)
          accumulated_dose – µSv (from data_buf RareData)
          battery          – % (from data_buf RareData, None most polls)
          temperature      – °C (from RareData or a cached TEMP_degC read)
        """
        # Read the primary radiation stream first.  A failed optional
        # temperature read must not discard a complete DATA_BUF sample.
        raw = await self._read_vs(VS.DATA_BUF)
        records = decode_data_buf(raw, self._base_time)
        buf_data = extract_sensor_values(records)
        now = asyncio.get_running_loop().time()
        if buf_data.temperature is not None:
            self._temperature = buf_data.temperature
            self._last_temperature_read = now
        elif (
            self._last_temperature_read is None
            or now - self._last_temperature_read >= _TEMPERATURE_INTERVAL
        ):
            # Set the deadline before sending, including failed attempts.
            # It survives reconnect so an optional transfer that consistently
            # stalls is retried at most once a minute instead of every poll.
            self._last_temperature_read = now
            try:
                values = await self._read_vsfr_batch([VSFR.TEMP_degC])
                if values[0] is not None:
                    self._temperature = values[0]
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("VSFR batch read for TEMP failed: %s", err)

        result = RadiaCodeData(
            dose_rate=buf_data.dose_rate,
            count_rate=buf_data.count_rate,
            accumulated_dose=buf_data.accumulated_dose,
            battery=buf_data.battery,
            temperature=self._temperature,
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

    async def get_serial_number(self) -> str:
        """Return the device serial number string, e.g. 'RC-103-012345'."""
        raw = await self._read_vs(VS.SERIAL_NUMBER)
        return decode_serial_number(raw)

    async def get_firmware_version(self) -> str:
        """Return the firmware version string, e.g. '4.8'."""
        payload = await self._execute(CMD.GET_VERSION)
        return parse_firmware_version(payload)

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
        async with self._cmd_lock:
            await self._refresh_spectrum_format_locked()

    async def _refresh_spectrum_format_locked(self) -> None:
        """Discover the format while holding the command lock."""
        raw = await self._read_vs_locked(VS.CONFIGURATION)
        config = raw.decode("cp1251", errors="replace")
        self._spectrum_format_version = parse_spec_format_version(config)
        self._spectrum_format_loaded = True
        _LOGGER.debug(
            "Spectrum format version: %d", self._spectrum_format_version
        )

    async def get_spectrum(self, accumulated: bool = False) -> Spectrum:
        """Read a complete, 1024-channel gamma spectrum.

        Discover the wire format once per BLE connection before decoding.
        Keep configuration and spectrum reads on that same connection.
        Reject an incomplete spectrum so callers can retain a previous
        complete snapshot and report the transfer error.
        """
        async with self._cmd_lock:
            if not self._spectrum_format_loaded:
                await self._refresh_spectrum_format_locked()
            vs_id = VS.SPEC_ACCUM if accumulated else VS.SPECTRUM
            raw = await self._read_vs_locked(vs_id)
            spectrum = decode_spectrum(raw, self._spectrum_format_version)
            if spectrum.truncated or len(spectrum.counts) != 1024:
                raise ValueError(
                    "Incomplete RadiaCode spectrum: "
                    f"received {len(spectrum.counts)} of 1024 channels"
                )
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
        self._last_notification = asyncio.get_running_loop().time()
        self._notify_event.set()

    # ── Low-level command execution ───────────────────────────────────────────

    async def _execute(self, cmd: int, args: bytes = b"") -> bytes:
        """Serialize commands, then return one complete validated reply."""
        async with self._cmd_lock:
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

        _LOGGER.debug(
            "_execute: cmd=%#06x seq=%d packet=%s (%d bytes)",
            cmd, seq, packet.hex(), len(packet),
        )

        try:
            # This includes writes: even a Write Without Response operation
            # can stall in the host/proxy backend before it queues the bytes.
            async with asyncio.timeout(_CMD_TIMEOUT):
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

                while True:
                    if self._disconnected_event.is_set() or self._client is not client:
                        raise ConnectionError(
                            f"BLE connection lost during command {cmd:#06x} (seq={seq})"
                        )
                    if self._response_error is not None:
                        raise self._response_error
                    if self._response_started and self._resp_total == 0:
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
            # Retiring also wakes waiters immediately and filters ghost
            # callbacks.  If a user disconnect already retired this client,
            # that caller owns its cleanup.
            if self._client is client:
                await self._retire_current_client()
            if isinstance(err, TimeoutError) and not str(err):
                raise TimeoutError(
                    f"Timed out during RadiaCode command {cmd:#06x} "
                    f"(seq={seq}): received {len(self._resp_buf)} bytes, "
                    f"missing {self._resp_total}"
                ) from err
            raise
        finally:
            self._expecting_response = False

    async def _read_vs(self, vs_id: int) -> bytes:
        """Execute a RD_VIRT_STRING command and return the VS data bytes."""
        async with self._cmd_lock:
            return await self._read_vs_locked(vs_id)

    async def _read_vs_locked(self, vs_id: int) -> bytes:
        """Read a complete virtual string while holding the command lock."""
        payload = await self._execute_locked(
            CMD.RD_VIRT_STRING, struct.pack("<I", int(vs_id))
        )
        return parse_vs_response(payload)

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
