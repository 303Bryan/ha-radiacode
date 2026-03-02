"""
RadiaCode BLE protocol — pure data layer (no BLE I/O).

Covers:
  - BLE UUID constants
  - Command/VS/VSFR ID enumerations
  - Command frame builder
  - Response parser and echo-header verifier
  - data_buf binary decoder
  - Sensor-value extractor
  - Serial-number decoder

Protocol reverse-engineered from cdump/radiacode (MIT) and
mkgeiger/RadiaCode (MIT). All framing is little-endian.

Wire format (request):
  [uint32_le: len(header+args)] [uint16_le: cmd_id] [0x00] [0x80+seq] [args...]

Wire format (response, arrives via BLE notifications):
  [int32_le: body_len] [body_len bytes: body]
  body[0:4] echoes the request header for verification.
"""

import datetime
import logging
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

_LOGGER = logging.getLogger(__name__)


# ── BLE UUIDs ─────────────────────────────────────────────────────────────────

SERVICE_UUID     = "e63215e5-7003-49d8-96b0-b024798fb901"
WRITE_CHAR_UUID  = "e63215e6-7003-49d8-96b0-b024798fb901"
NOTIFY_CHAR_UUID = "e63215e7-7003-49d8-96b0-b024798fb901"


# ── Command IDs ───────────────────────────────────────────────────────────────

class CMD(IntEnum):
    SET_EXCHANGE   = 0x0007  # required handshake on connect
    GET_VERSION    = 0x000A  # firmware version
    GET_SERIAL     = 0x000B  # hardware serial
    WR_VIRT_SFR    = 0x0825  # write virtual SFR (register)
    RD_VIRT_STRING = 0x0826  # read virtual string (data_buf, serial, etc.)
    SET_TIME       = 0x0A04  # set device clock


# ── Virtual String IDs (args to RD_VIRT_STRING) ───────────────────────────────

class VS(IntEnum):
    SERIAL_NUMBER = 0x08   # ASCII serial, e.g. "RC-103-012345"
    DATA_BUF      = 0x100  # real-time + accumulated sensor data stream


# ── Virtual SFR IDs (used with WR_VIRT_SFR) ──────────────────────────────────

class VSFR(IntEnum):
    DEVICE_TIME = 0x0504   # written as 0 after init


# ── Data types returned from data_buf ─────────────────────────────────────────

@dataclass
class RealTimeData:
    """Real-time radiation measurement (data_buf gid=0)."""
    dt: datetime.datetime
    count_rate: float      # counts per second (CPS)
    count_rate_err: float  # error, percent
    dose_rate: float       # µSv/h
    dose_rate_err: float   # error, percent


@dataclass
class RareData:
    """Periodic status snapshot (data_buf gid=3, appears ~every minute)."""
    dt: datetime.datetime
    dose: float            # accumulated dose, µSv
    charge_level: float    # battery, 0.0–1.0
    temperature: float     # device temperature, °C


@dataclass
class RadiaCodeData:
    """Aggregated sensor readings for one poll cycle."""
    dose_rate: Optional[float]         # µSv/h   (from RealTimeData)
    count_rate: Optional[float]        # CPS      (from RealTimeData)
    accumulated_dose: Optional[float]  # µSv      (from RareData)
    battery: Optional[float]           # percent  (from RareData)
    temperature: Optional[float]       # °C       (from RareData)


# ── Command builder ───────────────────────────────────────────────────────────

def build_command(cmd: int, seq: int, args: bytes = b"") -> bytes:
    """
    Build a fully framed command packet ready to write to the BLE characteristic.

    The packet is chunked in 18-byte writes by the caller (BLE MTU constraint
    observed in both cdump and mkgeiger implementations).
    """
    seq_byte = 0x80 + (seq % 32)
    header = struct.pack("<HBB", cmd, 0, seq_byte)
    payload = header + args
    return struct.pack("<I", len(payload)) + payload


# ── Response parsers ──────────────────────────────────────────────────────────

def parse_response_body(raw: bytes, expected_cmd: int, expected_seq: int) -> bytes:
    """
    Verify the 4-byte echo header and return the remaining response payload.

    The device echoes [cmd_lo, cmd_hi, 0x00, seq_byte] as the first four bytes
    of every response body; mismatches indicate a framing error.
    """
    if len(raw) < 4:
        raise ValueError(f"Response body too short: {len(raw)} bytes")

    cmd_lo, cmd_hi, _zero, seq_byte = struct.unpack_from("<BBBB", raw, 0)
    actual_cmd      = cmd_lo | (cmd_hi << 8)
    expected_seq_byte = 0x80 + (expected_seq % 32)

    if actual_cmd != expected_cmd or seq_byte != expected_seq_byte:
        raise ValueError(
            f"Response echo header mismatch: "
            f"cmd={actual_cmd:#06x} (want {expected_cmd:#06x}), "
            f"seq={seq_byte:#04x} (want {expected_seq_byte:#04x})"
        )

    return raw[4:]


def parse_vs_response(payload: bytes) -> bytes:
    """
    Parse the body of a RD_VIRT_STRING response into raw VS data bytes.

    After the 4-byte echo header (already stripped by parse_response_body):
      [uint32_le retcode] [uint32_le data_len] [data_len bytes]
    """
    if len(payload) < 8:
        raise ValueError(f"VS response payload too short: {len(payload)} bytes")

    retcode, data_len = struct.unpack_from("<II", payload, 0)
    if retcode != 1:
        raise ValueError(f"VS read failed: retcode={retcode}")

    data = payload[8:]

    # Firmware bug workaround (seen in cdump): stray trailing null byte
    if len(data) == data_len + 1 and data[-1] == 0x00:
        data = data[:-1]

    if len(data) < data_len:
        # Partial data — common when a large response is truncated by a
        # BT proxy with a limited notification buffer. Return what we have;
        # decode_data_buf handles truncated records gracefully.
        _LOGGER.warning(
            "VS data truncated: received %d of %d bytes", len(data), data_len
        )
    elif len(data) > data_len:
        raise ValueError(
            f"VS data length mismatch: got {len(data)} bytes, expected {data_len}"
        )

    return data


# Byte count of each sample in the eid=1 variable-length sample blocks.
# Defined at module level so it isn't reconstructed on every decoded record.
_SAMPLE_SIZES: dict[int, int] = {1: 8, 2: 16, 3: 14}


# ── Binary cursor helper ──────────────────────────────────────────────────────

class _Buf:
    """Minimal sequential binary reader (no external dependencies)."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def remaining(self) -> int:
        return len(self._data) - self._pos

    def unpack(self, fmt: str) -> tuple:
        sz = struct.calcsize(fmt)
        if self._pos + sz > len(self._data):
            raise ValueError(
                f"Need {sz} bytes for '{fmt}', only {self.remaining()} remaining"
            )
        result = struct.unpack_from(fmt, self._data, self._pos)
        self._pos += sz
        return result

    def skip(self, n: int) -> None:
        self._pos = min(self._pos + n, len(self._data))


# ── data_buf decoder ──────────────────────────────────────────────────────────

def decode_data_buf(data: bytes, base_time: datetime.datetime) -> list:
    """
    Decode the raw DATA_BUF byte stream into a list of typed records.

    Each record starts with a 7-byte header:
      [uint8 seq] [uint8 eid] [uint8 gid] [int32_le ts_offset_10ms]

    Timestamps are base_time + ts_offset * 10 ms.
    Decoding stops on unknown record types or sequence-number jumps.
    """
    buf = _Buf(data)
    records: list = []
    next_seq: Optional[int] = None

    while buf.remaining() >= 7:
        seq, eid, gid, ts_offset = buf.unpack("<BBBi")
        dt = base_time + datetime.timedelta(milliseconds=ts_offset * 10)

        if next_seq is not None and next_seq != seq:
            break  # sequence jump — stop decoding

        next_seq = (seq + 1) % 256

        try:
            if eid == 0 and gid == 0:       # GRP_RealTimeData
                count_rate, dose_rate, cps_err, dr_err, _flags, _rt = buf.unpack("<ffHHHB")
                records.append(RealTimeData(
                    dt=dt,
                    count_rate=count_rate,
                    count_rate_err=cps_err / 10,
                    dose_rate=dose_rate,
                    dose_rate_err=dr_err / 10,
                ))

            elif eid == 0 and gid == 1:     # GRP_RawData (skip)
                buf.unpack("<ff")

            elif eid == 0 and gid == 2:     # GRP_DoseRateDB (skip)
                buf.unpack("<IffHH")

            elif eid == 0 and gid == 3:     # GRP_RareData
                _dur, dose, temperature, charge_level, _flags = buf.unpack("<IfHHH")
                records.append(RareData(
                    dt=dt,
                    dose=dose,
                    charge_level=charge_level / 100,         # → 0.0–1.0
                    temperature=(temperature - 2000) / 100,  # → °C
                ))

            elif eid == 0 and gid == 4:     # GRP_UserData (skip)
                buf.unpack("<IffHH")

            elif eid == 0 and gid == 5:     # GRP_ScheduleData (skip)
                buf.unpack("<IffHH")

            elif eid == 0 and gid == 6:     # GRP_AccelData (skip)
                buf.unpack("<HHH")

            elif eid == 0 and gid == 7:     # GRP_Event (skip)
                buf.unpack("<BBH")

            elif eid == 0 and gid == 8:     # GRP_RawCountRate (skip)
                buf.unpack("<fH")

            elif eid == 0 and gid == 9:     # GRP_RawDoseRate (skip)
                buf.unpack("<fH")

            elif eid == 1 and gid in (1, 2, 3):  # variable-length sample blocks
                (samples_num,) = buf.unpack("<H")
                buf.unpack("<I")   # smpl_time_ms
                buf.skip(samples_num * _SAMPLE_SIZES[gid])

            else:
                break  # unknown record type; stop rather than misparse

        except ValueError:
            break  # truncated record; stop cleanly

    return records


def extract_sensor_values(records: list) -> RadiaCodeData:
    """
    Return the most recent sensor values from a decoded data_buf record list.

    RealTimeData appears on every ~1 s tick; RareData appears ~once per minute,
    so battery and accumulated_dose may be None if no RareData was in this batch.

    Records are iterated in arrival order; each match overwrites the previous,
    so the final values reflect the *last* (most recent) record of each type.
    """
    dose_rate: Optional[float] = None
    count_rate: Optional[float] = None
    accumulated_dose: Optional[float] = None
    battery: Optional[float] = None
    temperature: Optional[float] = None

    for r in records:
        if isinstance(r, RealTimeData):
            dose_rate = r.dose_rate
            count_rate = r.count_rate
        elif isinstance(r, RareData):
            accumulated_dose = r.dose
            battery = r.charge_level * 100   # convert fraction → percent
            temperature = r.temperature      # °C, already converted in decoder

    return RadiaCodeData(
        dose_rate=dose_rate,
        count_rate=count_rate,
        accumulated_dose=accumulated_dose,
        battery=battery,
        temperature=temperature,
    )


# ── Serial number decoder ─────────────────────────────────────────────────────

def decode_serial_number(data: bytes) -> str:
    """Decode VS.SERIAL_NUMBER bytes into a string, e.g. 'RC-103-012345'."""
    return data.decode("ascii").strip("\x00")
