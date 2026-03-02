"""
RadiaCode BLE client — async I/O layer built on bleak.

Usage pattern (connect → read → disconnect per poll cycle):

    client = RadiaCodeBLEClient()
    await client.connect(ble_device)
    data = await client.get_data()   # RadiaCodeData dataclass
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
    WRITE_CHAR_UUID,
    NOTIFY_CHAR_UUID,
    RadiaCodeData,
    build_command,
    parse_response_body,
    parse_vs_response,
    decode_data_buf,
    extract_sensor_values,
    decode_serial_number,
)

_LOGGER = logging.getLogger(__name__)

# Maximum bytes per write (BLE MTU constraint; both cdump and mkgeiger use 18)
_WRITE_CHUNK = 18

# Seconds to wait for a complete response notification before giving up.
# BT proxy round-trips add latency; 20 s is safe for all observed devices.
_CMD_TIMEOUT = 20.0


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

    # ── Connection management ─────────────────────────────────────────────────

    async def connect(self, ble_device: BLEDevice) -> None:
        """
        Connect to a RadiaCode device and run the required init sequence.

        The init sequence (SET_EXCHANGE → SET_TIME → DEVICE_TIME=0) must be
        completed before the device streams data_buf records.

        Raises:
            Exception: if bleak_retry_connector cannot establish a connection.
        """
        self._seq = 0

        self._client = await establish_connection(
            BleakClient,
            ble_device,
            ble_device.address,
            max_attempts=1,   # fail fast; HA coordinator retry loop handles backoff
            timeout=30.0,     # generous per-attempt timeout for BT proxy latency
        )

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

        _LOGGER.debug(
            "RadiaCode connected and initialised (%s)", ble_device.address
        )

    async def disconnect(self) -> None:
        """Disconnect from the device. Safe to call even if not connected."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Ignored error during disconnect: %s", err)
        self._client = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    # ── High-level API ────────────────────────────────────────────────────────

    async def get_data(self) -> RadiaCodeData:
        """
        Poll the device and return the latest sensor readings.

        Returns a RadiaCodeData with:
          dose_rate        – µSv/h  (None if no RealTimeData record in this batch)
          count_rate       – CPS    (None if no RealTimeData record in this batch)
          accumulated_dose – µSv    (None if no RareData record in this batch)
          battery          – %      (None if no RareData record in this batch)

        RealTimeData appears on every ~1 s tick buffered since last call.
        RareData appears ~once per minute, so battery/accumulated_dose
        will be None in many poll cycles and must be cached by the caller.
        """
        raw = await self._read_vs(VS.DATA_BUF)
        records = decode_data_buf(raw, self._base_time)
        result = extract_sensor_values(records)
        _LOGGER.debug(
            "get_data → dose_rate=%.4f µSv/h  count_rate=%.1f CPS  "
            "dose=%.4f µSv  battery=%s%%",
            result.dose_rate or 0,
            result.count_rate or 0,
            result.accumulated_dose or 0,
            f"{result.battery:.0f}" if result.battery is not None else "—",
        )
        return result

    async def get_serial_number(self) -> str:
        """Return the device serial number string, e.g. 'RC-103-012345'."""
        raw = await self._read_vs(VS.SERIAL_NUMBER)
        return decode_serial_number(raw)

    # ── Notification handler ──────────────────────────────────────────────────

    def _on_notify(self, _sender: int, data: bytearray) -> None:
        """
        Accumulate BLE notification packets into a complete response.

        First packet format: [int32_le body_len] [body_len bytes... (partial)]
        Subsequent packets:  continuation of the body.

        Sets _notify_event when all expected bytes have arrived.
        """
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
            self._notify_event.set()

    # ── Low-level command execution ───────────────────────────────────────────

    async def _execute(self, cmd: int, args: bytes = b"") -> bytes:
        """
        Send one command and return the response payload (echo header stripped).

        Steps:
          1. Allocate the next sequence number.
          2. Reset notification state.
          3. Write the framed packet in _WRITE_CHUNK-byte pieces.
          4. Await the notify event (up to _CMD_TIMEOUT seconds).
          5. Verify the echo header and return the payload.
        """
        seq = self._seq
        self._seq = (self._seq + 1) % 32

        packet = build_command(cmd, seq, args)

        # Reset reassembly state before writing (no race — same event loop)
        self._resp_buf = bytearray()
        self._resp_total = 0
        self._notify_event.clear()

        # Write in chunks to respect BLE MTU
        for offset in range(0, len(packet), _WRITE_CHUNK):
            chunk = packet[offset: offset + _WRITE_CHUNK]
            await self._client.write_gatt_char(WRITE_CHAR_UUID, chunk, response=False)

        # Wait for the complete response
        try:
            await asyncio.wait_for(self._notify_event.wait(), timeout=_CMD_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"No response to RadiaCode command {cmd:#06x} (seq={seq})"
            ) from exc

        body = bytes(self._resp_buf)
        return parse_response_body(body, cmd, seq)

    async def _read_vs(self, vs_id: int) -> bytes:
        """Execute a RD_VIRT_STRING command and return the VS data bytes."""
        payload = await self._execute(
            CMD.RD_VIRT_STRING, struct.pack("<I", int(vs_id))
        )
        return parse_vs_response(payload)
