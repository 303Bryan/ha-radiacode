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
declared number of bytes is received, then set _notify_event.

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
  • stop_notify() on the old client prevents ghost _on_notify callbacks
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
    SETTINGS_VSFR_IDS,
    WRITE_CHAR_UUID,
    NOTIFY_CHAR_UUID,
    RadiaCodeData,
    RadiaCodeSettings,
    build_command,
    parse_response_body,
    parse_vs_response,
    parse_vsfr_batch_response,
    parse_vsfr_read_response,
    parse_write_response,
    decode_data_buf,
    decode_settings,
    extract_sensor_values,
    decode_serial_number,
)

_LOGGER = logging.getLogger(__name__)

# Maximum bytes per write (BLE MTU constraint; both cdump and mkgeiger use 18)
_WRITE_CHUNK = 18

# Hard deadline — no single command should ever exceed this wall-clock time.
# With response=False writes completing instantly, this timeout only applies
# to waiting for notification replies.
_CMD_TIMEOUT = 10.0

# If no new BLE notification arrives for this many seconds *after* the first
# packet, assume the BT proxy notification buffer is exhausted and return
# whatever partial data we have.  Typical inter-packet gaps are <300 ms, so
# 2 s of silence is a clear stall signal.
_STALL_TIMEOUT = 2.0

# establish_connection() timeout per attempt.  15 s is generous for a BT
# proxy hop; if the ESP32 can't connect in this window the slot is likely
# stuck and we should fail fast so the coordinator can retry cleanly.
_CONNECT_TIMEOUT = 15.0


class RadiaCodeBLEClient:
    """
    Async BLE client for RadiaCode radiation detectors (RC-102/103/110).

    Replicates the transport behaviour of cdump/radiacode's Bluetooth class
    using bleak instead of bluepy, making it compatible with HA's Bluetooth
    proxy infrastructure.
    """

    def __init__(self) -> None:
        self._client: Optional[BleakClient] = None
        self._seq: int = 0
        self._base_time: Optional[datetime.datetime] = None

        # Notification reassembly state
        self._resp_buf: bytearray = bytearray()
        # _resp_total tracks the total frame bytes still expected:
        #   0 = idle (not waiting), >0 = bytes remaining
        self._resp_total: int = 0
        self._notify_event: asyncio.Event = asyncio.Event()

        # Guard: _on_notify ignores packets arriving when no command is in-flight.
        self._expecting_response: bool = False

        # Set by the BleakClient disconnect callback to unblock _execute()
        # immediately when the BLE link drops.
        self._disconnected_event: asyncio.Event = asyncio.Event()

        # Serialize BLE command execution.  Without this, a UI-triggered
        # write (e.g. switch toggle) can overlap with a coordinator poll,
        # corrupting the shared notification reassembly state.
        self._cmd_lock: asyncio.Lock = asyncio.Lock()

    # ── Connection management ─────────────────────────────────────────────────

    def _reset_notification_state(self) -> None:
        """Clear all notification reassembly state.  Safe to call at any time."""
        self._resp_buf = bytearray()
        self._resp_total = 0
        self._notify_event.clear()
        self._expecting_response = False
        self._disconnected_event.clear()

    async def connect(self, ble_device: BLEDevice) -> None:
        """
        Connect to a RadiaCode device and run the required init sequence.

        If a previous BleakClient exists (stale connection), it is torn down
        first — notifications are stopped and the client disconnected — to
        prevent ghost _on_notify callbacks from a dead transport.

        The init sequence (SET_EXCHANGE → SET_TIME → DEVICE_TIME=0) must be
        completed before the device streams data_buf records.

        Raises:
            Exception: if bleak_retry_connector cannot establish a connection.
        """
        # ── Tear down any previous client to prevent double _on_notify ──────
        old = self._client
        self._client = None          # is_connected → False immediately
        if old is not None:
            try:
                await asyncio.wait_for(
                    old.stop_notify(NOTIFY_CHAR_UUID), timeout=5.0
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(old.disconnect(), timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
            _LOGGER.debug("Tore down previous BLE client before reconnect")

        # ── Reset all state for the new connection ──────────────────────────
        self._seq = 0
        self._reset_notification_state()

        self._client = await establish_connection(
            BleakClient,
            ble_device,
            ble_device.address,
            max_attempts=2,   # allow one internal retry; coordinator adds another layer
            timeout=_CONNECT_TIMEOUT,
        )

        self._client.set_disconnected_callback(self._on_ble_disconnect)
        await self._client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)

        # ── Init sequence (mirrors cdump RadiaCode.__init__) ──────────────────
        # 1. Handshake — device expects this exact payload before responding to data
        await self._execute(CMD.SET_EXCHANGE, b"\x01\xff\x12\xff")

        # 2. Sync device clock to host time
        now = datetime.datetime.now()
        time_payload = struct.pack(
            "<BBBBBBBB",
            now.day, now.month, now.year - 2000, 0,
            now.second, now.minute, now.hour, 0,
        )
        await self._execute(CMD.SET_TIME, time_payload)

        # 3. Zero out DEVICE_TIME VSFR
        await self._execute(
            CMD.WR_VIRT_SFR,
            struct.pack("<II", int(VSFR.DEVICE_TIME), 0),
        )

        # base_time anchors the 10 ms timestamp offsets inside data_buf records.
        # cdump sets this to now+128 s during init.
        self._base_time = datetime.datetime.now() + datetime.timedelta(seconds=128)

        # Drain any stale data_buf records accumulated while disconnected.
        # The first read after a fresh connection returns ALL records buffered
        # on the device (often 1000+ bytes / 50+ notification packets), which
        # overflows the ESPHome BT proxy's notification buffer.  By draining
        # here, subsequent poll reads are small (5 s worth of data ≈ 5 records).
        # A partial/truncated drain is fine — the device clears sent records.
        try:
            await self._read_vs(VS.DATA_BUF)
            _LOGGER.debug("Drained stale data_buf after init")
        except Exception:  # noqa: BLE001
            _LOGGER.debug("data_buf drain read failed (non-fatal)")

        _LOGGER.debug(
            "RadiaCode connected and initialised (%s)", ble_device.address
        )

    async def disconnect(self) -> None:
        """Disconnect from the device.  Safe to call even if not connected.

        Sets _client = None *before* the potentially slow BleakClient.disconnect()
        so that is_connected returns False immediately, preventing the coordinator
        from trying to reuse a half-dead connection in a concurrent poll.
        """
        client = self._client
        self._client = None                     # is_connected → False immediately
        self._reset_notification_state()

        if client is None:
            return

        try:
            await asyncio.wait_for(
                client.stop_notify(NOTIFY_CHAR_UUID), timeout=5.0
            )
        except Exception:  # noqa: BLE001
            pass

        try:
            await asyncio.wait_for(client.disconnect(), timeout=5.0)
        except asyncio.TimeoutError:
            _LOGGER.debug("Disconnect timed out after 5s — forcing cleanup")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Ignored error during disconnect: %s", err)

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    def _on_ble_disconnect(self, _client: BleakClient) -> None:
        """Called by bleak when the BLE transport disconnects.

        Sets _disconnected_event so that any in-flight _execute() waiting for
        notifications can bail out immediately instead of waiting for the full
        CMD_TIMEOUT.
        """
        _LOGGER.debug("BLE disconnect callback fired")
        self._disconnected_event.set()
        # Also set the notify event so _execute() unblocks from its wait loop.
        self._notify_event.set()

    # ── High-level API ────────────────────────────────────────────────────────

    async def get_data(self) -> RadiaCodeData:
        """
        Poll the device and return the latest sensor readings.

        Reads VSFR registers for dose_rate, accumulated_dose, and temperature,
        then reads data_buf for count_rate (float CPS from RealTimeData) and
        battery (from RareData, appears ~once per minute).

        VSFR reading strategy:
          1. Try a batch read for all three registers (one BLE round-trip).
          2. For any register the device marks as invalid in the batch
             response (DR_uR_h and DS_uR are consistently invalid on
             current firmware), fall back to individual RD_VIRT_SFR reads.
          3. As a last resort, use data_buf values (though dose_rate from
             data_buf is always 0.0 on current firmware).

        Returns a RadiaCodeData with:
          dose_rate        – µSv/h   (from VSFR DR_uR_h, converted µR/h → µSv/h)
          count_rate       – CPS     (from data_buf RealTimeData, float precision)
          accumulated_dose – µSv     (from VSFR DS_uR, converted µR → µSv)
          battery          – %       (from data_buf RareData, None most polls)
          temperature      – °C      (from VSFR TEMP_degC)
        """
        # 1. VSFR batch read — try to get all three in one command.
        dose_rate: Optional[float] = None
        accumulated_dose: Optional[float] = None
        temperature: Optional[float] = None
        try:
            vsfr_ids = [VSFR.DR_uR_h, VSFR.DS_uR, VSFR.TEMP_degC]
            values = await self._read_vsfr_batch(vsfr_ids)
            if values[0] is not None:
                dose_rate = values[0] / 100.0         # µR/h → µSv/h
            if values[1] is not None:
                accumulated_dose = values[1] / 100.0  # µR → µSv
            if values[2] is not None:
                temperature = values[2]               # already °C
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("VSFR batch read failed: %s", err)

        # 2. Individual VSFR reads for registers the batch couldn't provide.
        #    DR_uR_h and DS_uR are consistently marked invalid in batch reads
        #    on current firmware, but individual RD_VIRT_SFR (0x0824) works.
        if dose_rate is None:
            try:
                raw_val = await self._read_vsfr(VSFR.DR_uR_h)
                dose_rate = raw_val / 100.0           # µR/h → µSv/h
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Individual VSFR read DR_uR_h failed: %s", err)

        if accumulated_dose is None:
            try:
                raw_val = await self._read_vsfr(VSFR.DS_uR)
                accumulated_dose = raw_val / 100.0    # µR → µSv
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Individual VSFR read DS_uR failed: %s", err)

        # 3. data_buf read — count_rate (float CPS) and battery (from RareData).
        raw = await self._read_vs(VS.DATA_BUF)
        records = decode_data_buf(raw, self._base_time)
        buf_data = extract_sensor_values(records)

        # Prefer VSFR values; fall back to data_buf if both read methods failed.
        result = RadiaCodeData(
            dose_rate=dose_rate if dose_rate is not None else buf_data.dose_rate,
            count_rate=buf_data.count_rate,
            accumulated_dose=(
                accumulated_dose
                if accumulated_dose is not None
                else buf_data.accumulated_dose
            ),
            battery=buf_data.battery,
            temperature=(
                temperature if temperature is not None else buf_data.temperature
            ),
        )

        _LOGGER.debug(
            "get_data → dose_rate=%.4f µSv/h  count_rate=%.1f CPS  "
            "dose=%.4f µSv  battery=%s%%  temp=%s°C",
            result.dose_rate or 0,
            result.count_rate or 0,
            result.accumulated_dose or 0,
            f"{result.battery:.0f}" if result.battery is not None else "—",
            f"{result.temperature:.1f}" if result.temperature is not None else "—",
        )
        return result

    async def get_serial_number(self) -> str:
        """Return the device serial number string, e.g. 'RC-103-012345'."""
        raw = await self._read_vs(VS.SERIAL_NUMBER)
        return decode_serial_number(raw)

    async def get_settings(self) -> RadiaCodeSettings:
        """Read all device settings via a single VSFR batch read.

        Returns a RadiaCodeSettings with current display, sound, vibration,
        and alarm threshold values.  The response is ~60 bytes (fits in one
        BLE notification), so this never hits BT proxy buffer limits.
        """
        values = await self._read_vsfr_batch(SETTINGS_VSFR_IDS)
        return decode_settings(values)

    async def write_vsfr(self, vsfr_id: int, value: int) -> bool:
        """Write a single VSFR register.  Returns True on success.

        The *value* is always packed as uint32.  For bool registers pass 1/0;
        for byte registers (e.g. brightness 0-9) pass the integer directly.
        """
        args = struct.pack("<II", vsfr_id, value)
        payload = await self._execute(CMD.WR_VIRT_SFR, args)
        return parse_write_response(payload)

    # ── Notification handler ──────────────────────────────────────────────────

    def _on_notify(self, _sender: int, data: bytearray) -> None:
        """
        Accumulate BLE notification packets into a complete response.

        First packet format: [int32_le body_len] [body_len bytes... (partial)]
        Subsequent packets:  continuation of the body.

        Sets _notify_event when all expected bytes have arrived.
        """
        # Guard: ignore notifications that arrive after the command has been
        # completed/timed-out, or from a ghost callback of a dead BleakClient.
        if not self._expecting_response:
            _LOGGER.debug(
                "_on_notify: ignoring %d bytes (no command in-flight)", len(data)
            )
            return

        _LOGGER.debug(
            "_on_notify: %d bytes, resp_total_before=%d, buf_len=%d, data=%s",
            len(data),
            self._resp_total,
            len(self._resp_buf),
            data[:16].hex(),
        )

        if self._resp_total == 0:
            # First packet — extract the declared body length
            if len(data) < 4:
                _LOGGER.warning(
                    "Undersized first BLE notification: %d bytes, need ≥4", len(data)
                )
                return

            (body_len,) = struct.unpack_from("<i", data, 0)
            # Total frame = 4-byte header + body
            self._resp_total = 4 + body_len
            self._resp_buf = bytearray(data[4:])
            _LOGGER.debug(
                "_on_notify: first packet, body_len=%d, resp_total=%d",
                body_len,
                self._resp_total,
            )
        else:
            # Continuation packet
            self._resp_buf.extend(data)

        self._resp_total -= len(data)

        if self._resp_total < 0:
            _LOGGER.warning(
                "BLE response overflow by %d bytes; clamping", -self._resp_total
            )
            self._resp_total = 0

        if self._resp_total == 0:
            _LOGGER.debug("_on_notify: response complete, setting event")
            self._notify_event.set()

    # ── Low-level command execution ───────────────────────────────────────────

    async def _execute(self, cmd: int, args: bytes = b"") -> bytes:
        """
        Send one command and return the response payload (echo header stripped).

        The _cmd_lock ensures only one command is in-flight at a time.
        Without this, a UI-triggered write (switch toggle, number change)
        could overlap with a coordinator poll, corrupting the shared
        notification reassembly state (_resp_buf / _notify_event).

        Steps:
          1. Acquire _cmd_lock (serialize with other commands).
          2. Allocate the next sequence number.
          3. Reset notification state.
          4. Write the framed packet in _WRITE_CHUNK-byte pieces.
          5. Await the notify event (up to _CMD_TIMEOUT seconds).
          6. Verify the echo header and return the payload.
        """
        async with self._cmd_lock:
            return await self._execute_locked(cmd, args)

    async def _execute_locked(self, cmd: int, args: bytes = b"") -> bytes:
        """Inner _execute body, called with _cmd_lock held."""
        seq = self._seq
        self._seq = (self._seq + 1) % 32

        packet = build_command(cmd, seq, args)

        # Reset reassembly state before writing (no race — same event loop)
        self._resp_buf = bytearray()
        self._resp_total = 0
        self._notify_event.clear()
        self._expecting_response = True

        _LOGGER.debug(
            "_execute: cmd=%#06x seq=%d packet=%s (%d bytes)",
            cmd, seq, packet.hex(), len(packet),
        )

        # Write in chunks to respect BLE MTU.
        # Use response=False (ATT Write Command / Write Without Response).
        # Through ESPHome BT proxies, ATT Write Requests (response=True)
        # can hang for 10+ seconds because the Write Response from the
        # device doesn't reliably traverse the proxy relay — even while
        # the device successfully processes the command and sends
        # notification data.  Write Without Response is fire-and-forget;
        # we verify the command was processed by the notification reply.
        for offset in range(0, len(packet), _WRITE_CHUNK):
            chunk = packet[offset: offset + _WRITE_CHUNK]
            _LOGGER.debug("_execute: writing chunk offset=%d len=%d", offset, len(chunk))
            if self._disconnected_event.is_set():
                raise ConnectionError(
                    f"BLE disconnected before write for cmd {cmd:#06x} (seq={seq})"
                )
            await self._client.write_gatt_char(WRITE_CHAR_UUID, chunk, response=False)
            _LOGGER.debug("_execute: chunk write complete")

        _LOGGER.debug("_execute: all chunks written, waiting for notify")

        # Wait for the complete response with stall detection.
        # ESPHome BT proxies can only forward ~28 BLE notification packets
        # before their buffer fills. For large DATA_BUF responses (50+
        # packets), notifications stop mid-stream. Instead of waiting the
        # full hard timeout, we detect the stall (no new data for
        # _STALL_TIMEOUT seconds) and return what we have.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _CMD_TIMEOUT
        last_buf_len = 0
        last_growth = loop.time()

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            if self._disconnected_event.is_set():
                _LOGGER.debug(
                    "_execute: BLE disconnected while waiting for notify "
                    "(cmd=%#06x, seq=%d)", cmd, seq,
                )
                break
            try:
                await asyncio.wait_for(
                    self._notify_event.wait(),
                    timeout=min(0.5, remaining),
                )
                break  # Complete response received
            except asyncio.TimeoutError:
                now = loop.time()
                current_len = len(self._resp_buf)
                if current_len > last_buf_len:
                    last_buf_len = current_len
                    last_growth = now
                elif current_len > 0 and (now - last_growth) >= _STALL_TIMEOUT:
                    # Notifications stopped flowing — BT proxy buffer exhausted
                    break

        # Command done — stop accepting notifications for this round.
        self._expecting_response = False

        # If the disconnect callback set _notify_event, distinguish from
        # a genuine complete response by checking _disconnected_event.
        if self._notify_event.is_set() and not self._disconnected_event.is_set():
            body = bytes(self._resp_buf)
            return parse_response_body(body, cmd, seq)

        if self._disconnected_event.is_set() and len(self._resp_buf) < 4:
            raise ConnectionError(
                f"BLE connection lost during command {cmd:#06x} (seq={seq})"
            )

        if len(self._resp_buf) >= 4:
            _LOGGER.warning(
                "Partial response for cmd %#06x (seq=%d): "
                "received %d bytes, still missing %d; using partial data",
                cmd, seq, len(self._resp_buf), self._resp_total,
            )
            body = bytes(self._resp_buf)
            return parse_response_body(body, cmd, seq)

        raise TimeoutError(
            f"No response to RadiaCode command {cmd:#06x} (seq={seq})"
        )

    async def _read_vs(self, vs_id: int) -> bytes:
        """Execute a RD_VIRT_STRING command and return the VS data bytes."""
        payload = await self._execute(
            CMD.RD_VIRT_STRING, struct.pack("<I", int(vs_id))
        )
        return parse_vs_response(payload)

    async def _read_vsfr_batch(self, vsfr_ids: list[int]) -> list[int | float | None]:
        """Read multiple VSFR registers in a single command.

        Returns decoded values in the same order as *vsfr_ids*.  Values
        for registers the device marks as invalid are returned as None.
        The response is very small (~20 bytes for 3 registers), so it
        never hits BT proxy notification buffer limits.
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
