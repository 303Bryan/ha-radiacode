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
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Optional

_LOGGER = logging.getLogger(__name__)


# ── BLE UUIDs ─────────────────────────────────────────────────────────────────

SERVICE_UUID     = "e63215e5-7003-49d8-96b0-b024798fb901"
WRITE_CHAR_UUID  = "e63215e6-7003-49d8-96b0-b024798fb901"
NOTIFY_CHAR_UUID = "e63215e7-7003-49d8-96b0-b024798fb901"


# ── Command IDs ───────────────────────────────────────────────────────────────

class CMD(IntEnum):
    SET_EXCHANGE      = 0x0007  # required handshake on connect
    GET_VERSION       = 0x000A  # firmware version
    GET_SERIAL        = 0x000B  # hardware serial
    RD_VIRT_SFR       = 0x0824  # read single virtual SFR (register)
    WR_VIRT_SFR       = 0x0825  # write virtual SFR (register)
    RD_VIRT_STRING    = 0x0826  # read virtual string (data_buf, serial, etc.)
    RD_VIRT_SFR_BATCH = 0x082A  # read multiple VSFRs in one command
    SET_TIME          = 0x0A04  # set device clock


# ── Virtual String IDs (args to RD_VIRT_STRING) ───────────────────────────────

class VS(IntEnum):
    SERIAL_NUMBER = 0x08   # ASCII serial, e.g. "RC-103-012345"
    DATA_BUF      = 0x100  # real-time + accumulated sensor data stream


# ── Virtual SFR IDs (used with WR_VIRT_SFR) ──────────────────────────────────

class VSFR(IntEnum):
    # Init / time
    DEVICE_TIME    = 0x0504   # written as 0 after init

    # Display settings
    DISP_BRT       = 0x0511   # brightness 0-9 (byte)
    DISP_OFF_TIME  = 0x0513   # auto-off: 0=5s 1=10s 2=15s 3=30s
    DISP_ON        = 0x0514   # display on/off (bool)
    DISP_DIR       = 0x0515   # direction: 0=Auto 1=Right 2=Left
    DISP_BACKLT_ON = 0x0516   # backlight on/off (bool)

    # Sound / vibration
    SOUND_ON       = 0x0522   # sound on/off (bool)
    VIBRO_ON       = 0x0531   # vibration on/off (bool)

    # Alarm thresholds
    DR_LEV1_uR_h   = 0x8000  # dose rate alarm L1, µR/h
    DR_LEV2_uR_h   = 0x8001  # dose rate alarm L2, µR/h
    DOSE_RESET     = 0x8007  # write 1 to reset accumulated dose
    CR_LEV1_cp10s  = 0x8008  # count rate alarm L1, counts/10s
    CR_LEV2_cp10s  = 0x8009  # count rate alarm L2, counts/10s
    DS_LEV1_uR     = 0x8014  # accumulated dose alarm L1, µR
    DS_LEV2_uR     = 0x8015  # accumulated dose alarm L2, µR

    # Sensor registers (read-only)
    CPS            = 0x8020   # counts per second (uint32)
    DR_uR_h        = 0x8021   # dose rate in µR/h (uint32)
    DS_uR          = 0x8022   # accumulated dose in µR (uint32)
    TEMP_degC      = 0x8024   # temperature in °C (float)


# Struct format for reinterpreting raw uint32 VSFR values into typed values.
# All settings registers are uint32 on the wire (bools and bytes packed in uint32).
_VSFR_FORMATS: dict[int, str] = {
    VSFR.DEVICE_TIME:    "I",
    VSFR.DISP_BRT:       "I",
    VSFR.DISP_OFF_TIME:  "I",
    VSFR.DISP_ON:        "I",
    VSFR.DISP_DIR:       "I",
    VSFR.DISP_BACKLT_ON: "I",
    VSFR.SOUND_ON:       "I",
    VSFR.VIBRO_ON:       "I",
    VSFR.DR_LEV1_uR_h:  "I",
    VSFR.DR_LEV2_uR_h:  "I",
    VSFR.CR_LEV1_cp10s:  "I",
    VSFR.CR_LEV2_cp10s:  "I",
    VSFR.DS_LEV1_uR:     "I",
    VSFR.DS_LEV2_uR:     "I",
    VSFR.CPS:            "I",
    VSFR.DR_uR_h:        "I",
    VSFR.DS_uR:          "I",
    VSFR.TEMP_degC:      "f",
}

# VSFR IDs to batch-read for device settings (order matches RadiaCodeSettings).
# DOSE_RESET is excluded — it is write-only / stateless.
SETTINGS_VSFR_IDS: list[int] = [
    VSFR.SOUND_ON,
    VSFR.VIBRO_ON,
    VSFR.DISP_ON,
    VSFR.DISP_BACKLT_ON,
    VSFR.DISP_BRT,
    VSFR.DISP_OFF_TIME,
    VSFR.DISP_DIR,
    VSFR.DR_LEV1_uR_h,
    VSFR.DR_LEV2_uR_h,
    VSFR.DS_LEV1_uR,
    VSFR.DS_LEV2_uR,
    VSFR.CR_LEV1_cp10s,
    VSFR.CR_LEV2_cp10s,
]


# ── Data types returned from data_buf ─────────────────────────────────────────

@dataclass
class RealTimeData:
    """Real-time radiation measurement (data_buf gid=0, appears ~every second)."""
    dt: datetime.datetime
    count_rate: float      # counts per second (CPS)
    count_rate_err: float  # error, percent
    dose_rate: float       # dose rate (float, units vary by firmware)
    dose_rate_err: float   # error, percent


@dataclass
class DoseRateDB:
    """Dose-rate database record (data_buf gid=2, appears periodically)."""
    dt: datetime.datetime
    count: int             # total counts in measurement period
    count_rate: float      # counts per second (CPS)
    dose_rate: float       # dose rate (float, units vary by firmware)
    dose_rate_err: float   # error, percent


@dataclass
class RawData:
    """Raw radiation data (data_buf gid=1, appears periodically)."""
    dt: datetime.datetime
    count_rate: float      # counts per second (CPS)
    dose_rate: float       # dose rate (float, units vary by firmware)


@dataclass
class RareData:
    """Periodic status snapshot (data_buf gid=3, appears ~every minute)."""
    dt: datetime.datetime
    dose: float            # accumulated dose, µSv
    charge_level: float    # battery, 0–100 percent
    temperature: float     # device temperature, °C


@dataclass
class RadiaCodeData:
    """Aggregated sensor readings for one poll cycle.

    All dose values are already converted to µSv-based units by
    extract_sensor_values (raw data_buf values are in Roentgen).
    """
    dose_rate: Optional[float]         # µSv/h   (converted from R/h)
    count_rate: Optional[float]        # CPS      (from RealTimeData)
    accumulated_dose: Optional[float]  # µSv      (converted from R)
    battery: Optional[float]           # percent  (from RareData)
    temperature: Optional[float]       # °C       (from RareData)


@dataclass
class RadiaCodeSettings:
    """Current device settings, read from VSFR batch.

    Fields are in the same order as SETTINGS_VSFR_IDS.  Alarm thresholds
    are stored in device-native units; the HA entity layer converts.
    """
    sound_on: Optional[bool] = None
    vibro_on: Optional[bool] = None
    display_on: Optional[bool] = None
    display_backlight_on: Optional[bool] = None
    display_brightness: Optional[int] = None        # 0-9
    display_off_time: Optional[int] = None           # 0=5s 1=10s 2=15s 3=30s
    display_direction: Optional[int] = None          # 0=Auto 1=Right 2=Left
    dr_alarm_level1: Optional[int] = None            # µR/h
    dr_alarm_level2: Optional[int] = None            # µR/h
    ds_alarm_level1: Optional[int] = None            # µR
    ds_alarm_level2: Optional[int] = None            # µR
    cr_alarm_level1: Optional[int] = None            # counts/10s
    cr_alarm_level2: Optional[int] = None            # counts/10s


def decode_settings(values: list[int | float | None]) -> RadiaCodeSettings:
    """Convert raw VSFR batch values (in SETTINGS_VSFR_IDS order) to settings.

    Values may be None if the device reported the register as invalid
    in the batch response; those fields keep their dataclass default (None).
    """
    return RadiaCodeSettings(
        sound_on=bool(values[0]) if values[0] is not None else None,
        vibro_on=bool(values[1]) if values[1] is not None else None,
        display_on=bool(values[2]) if values[2] is not None else None,
        display_backlight_on=bool(values[3]) if values[3] is not None else None,
        display_brightness=int(values[4]) if values[4] is not None else None,
        display_off_time=int(values[5]) if values[5] is not None else None,
        display_direction=int(values[6]) if values[6] is not None else None,
        dr_alarm_level1=int(values[7]) if values[7] is not None else None,
        dr_alarm_level2=int(values[8]) if values[8] is not None else None,
        ds_alarm_level1=int(values[9]) if values[9] is not None else None,
        ds_alarm_level2=int(values[10]) if values[10] is not None else None,
        cr_alarm_level1=int(values[11]) if values[11] is not None else None,
        cr_alarm_level2=int(values[12]) if values[12] is not None else None,
    )


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


def parse_vsfr_batch_response(
    payload: bytes, vsfr_ids: list[int]
) -> list[int | float | None]:
    """Parse a RD_VIRT_SFR_BATCH response into decoded register values.

    The response after the 4-byte echo header (already stripped):
      [uint32_le valid_flags] [uint32_le val0] [uint32_le val1] ...

    The device only includes uint32 values for registers whose
    corresponding bit is set in *valid_flags*.  Registers marked
    invalid (bit=0) are omitted from the response payload entirely,
    and this function returns ``None`` for those positions.

    Each raw uint32 value is reinterpreted through ``_VSFR_FORMATS``
    (e.g. TEMP_degC is stored as a float in the uint32 bit pattern).

    Returns a list of decoded values (or None) in the same order as
    *vsfr_ids*.
    """
    if len(payload) < 4:
        raise ValueError(
            f"VSFR batch response too short: {len(payload)} bytes, need ≥4"
        )

    (valid_flags,) = struct.unpack_from("<I", payload, 0)

    # Count how many registers the device actually included values for.
    n_valid = bin(valid_flags).count("1")
    needed = 4 + 4 * n_valid
    if len(payload) < needed:
        raise ValueError(
            f"VSFR batch response too short: {len(payload)} bytes, "
            f"need {needed} for {n_valid} valid registers"
        )

    nvsfr = len(vsfr_ids)
    result: list[int | float | None] = []
    offset = 4
    for i in range(nvsfr):
        if not (valid_flags & (1 << i)):
            result.append(None)
            continue
        (raw,) = struct.unpack_from("<I", payload, offset)
        fmt = _VSFR_FORMATS.get(vsfr_ids[i], "I")
        (value,) = struct.unpack(f"<{fmt}", struct.pack("<I", raw))
        result.append(value)
        offset += 4

    return result


def parse_write_response(payload: bytes) -> bool:
    """Parse a WR_VIRT_SFR response.  Returns True if the write succeeded."""
    if len(payload) < 4:
        raise ValueError(f"Write response too short: {len(payload)} bytes")
    (retcode,) = struct.unpack_from("<I", payload, 0)
    return retcode == 1


def parse_vsfr_read_response(payload: bytes, vsfr_id: int) -> int | float:
    """Parse a RD_VIRT_SFR (individual register read) response.

    After the 4-byte echo header (already stripped by parse_response_body):
      [uint32_le retcode] [uint32_le raw_value]

    The raw uint32 value is reinterpreted through ``_VSFR_FORMATS``
    (e.g. TEMP_degC is stored as a float in the uint32 bit pattern).

    Raises ValueError if the response is too short or the device
    returned a non-success retcode.
    """
    if len(payload) < 8:
        raise ValueError(
            f"VSFR read response too short: {len(payload)} bytes, need 8"
        )
    retcode, raw = struct.unpack_from("<II", payload, 0)
    if retcode != 1:
        raise ValueError(f"VSFR read failed: retcode={retcode}")
    fmt = _VSFR_FORMATS.get(vsfr_id, "I")
    (value,) = struct.unpack(f"<{fmt}", struct.pack("<I", raw))
    return value


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

    # Diagnostic counters for record types
    gid_counts: dict[int, int] = {}

    _LOGGER.debug(
        "decode_data_buf: %d bytes, first_hex=%s",
        len(data), data[:60].hex() if data else "",
    )

    while buf.remaining() >= 7:
        seq, eid, gid, ts_offset = buf.unpack("<BBBi")
        dt = base_time + datetime.timedelta(milliseconds=ts_offset * 10)

        if next_seq is not None and next_seq != seq:
            break  # sequence jump — stop decoding

        next_seq = (seq + 1) % 256
        gid_counts[gid] = gid_counts.get(gid, 0) + 1

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

            elif eid == 0 and gid == 1:     # GRP_RawData
                count_rate, dose_rate = buf.unpack("<ff")
                records.append(RawData(
                    dt=dt,
                    count_rate=count_rate,
                    dose_rate=dose_rate,
                ))

            elif eid == 0 and gid == 2:     # GRP_DoseRateDB
                count, count_rate, dose_rate, dr_err, _flags = buf.unpack("<IffHH")
                records.append(DoseRateDB(
                    dt=dt,
                    count=count,
                    count_rate=count_rate,
                    dose_rate=dose_rate,
                    dose_rate_err=dr_err / 10,
                ))

            elif eid == 0 and gid == 3:     # GRP_RareData
                _dur, dose, temperature, charge_level, _flags = buf.unpack("<IfHHH")
                records.append(RareData(
                    dt=dt,
                    dose=dose,
                    charge_level=charge_level / 100,         # → 0–100 percent
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

    # Log record type distribution and dose_rate values for diagnostics.
    # Raw dose_rate is in R/h; show converted µSv/h (×10000) for readability.
    _LOGGER.debug("decode_data_buf: gid_counts=%s, total_records=%d", gid_counts, len(records))
    for r in records:
        if isinstance(r, RealTimeData):
            _LOGGER.debug(
                "  RealTimeData: count_rate=%.2f dose_rate=%.6e R/h (%.4f µSv/h) err=%.1f%%",
                r.count_rate, r.dose_rate, r.dose_rate * 10_000, r.dose_rate_err,
            )
        elif isinstance(r, DoseRateDB):
            _LOGGER.debug(
                "  DoseRateDB: count=%d count_rate=%.2f dose_rate=%.6e R/h (%.4f µSv/h) err=%.1f%%",
                r.count, r.count_rate, r.dose_rate, r.dose_rate * 10_000, r.dose_rate_err,
            )
        elif isinstance(r, RawData):
            _LOGGER.debug(
                "  RawData: count_rate=%.2f dose_rate=%.6e R/h (%.4f µSv/h)",
                r.count_rate, r.dose_rate, r.dose_rate * 10_000,
            )

    return records


def extract_sensor_values(records: list) -> RadiaCodeData:
    """
    Return the most recent sensor values from a decoded data_buf record list.

    **Unit conversion (confirmed via cdump/radiacode examples):**
    data_buf dose_rate floats are in **R/h** (Roentgen per hour).
    Accumulated dose (RareData.dose) is in **R** (Roentgen).

    The device uses a fixed 100 µR = 1 µSv approximation, so:
      µSv/h = dose_rate_Rh × 1 000 000  (→ µR/h)  ÷ 100  = × 10 000
      µSv   = dose_R       × 1 000 000  (→ µR)    ÷ 100  = × 10 000

    Reference: cdump narodmon.py uses ``1_000_000 * dose_rate`` → µR/h;
               cdump webserver.py uses ``10_000 * dose_rate`` → µSv/h.

    Dose rate and count rate are extracted from all record types that carry
    them (RealTimeData, DoseRateDB, RawData), preferring the most recent
    non-zero value.  RareData appears ~once per minute, so battery and
    accumulated_dose may be None if no RareData was in this batch.

    Records are iterated in arrival order; each match overwrites the previous,
    so the final values reflect the *last* (most recent) record of each type.
    """
    # Conversion factor: R/h → µSv/h  (and R → µSv).
    _R_TO_uSv = 10_000

    dose_rate: Optional[float] = None
    count_rate: Optional[float] = None
    accumulated_dose: Optional[float] = None
    battery: Optional[float] = None
    temperature: Optional[float] = None

    for r in records:
        if isinstance(r, RealTimeData):
            count_rate = r.count_rate
            dose_rate = r.dose_rate * _R_TO_uSv  # R/h → µSv/h
        elif isinstance(r, DoseRateDB):
            dose_rate = r.dose_rate * _R_TO_uSv  # R/h → µSv/h
            if count_rate is None:
                count_rate = r.count_rate
        elif isinstance(r, RawData):
            dose_rate = r.dose_rate * _R_TO_uSv  # R/h → µSv/h
            if count_rate is None:
                count_rate = r.count_rate
        elif isinstance(r, RareData):
            accumulated_dose = r.dose * _R_TO_uSv  # R → µSv
            battery = r.charge_level           # already 0–100 percent
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


# ── Firmware version decoder ─────────────────────────────────────────────────

def parse_firmware_version(data: bytes) -> str:
    """Parse a GET_VERSION response into a ``'major.minor'`` string.

    The response contains two version tuples (boot + target/firmware).
    We extract the **target** version which is the user-visible firmware.

    Wire layout (from cdump ``fw_version()``):
      [uint16 boot_minor] [uint16 boot_major]
      [uint8 date_len] [ascii boot_date …]
      [uint16 target_minor] [uint16 target_major]
      [uint8 date_len] [ascii target_date …]
    """
    # Skip boot version: 4 bytes (minor + major) + length-prefixed string.
    offset = 4  # boot_minor(H) + boot_major(H)
    if offset >= len(data):
        return "unknown"
    boot_date_len = data[offset]
    offset += 1 + boot_date_len

    # Read target (firmware) version.
    if offset + 4 > len(data):
        return "unknown"
    target_minor, target_major = struct.unpack_from("<HH", data, offset)
    return f"{target_major}.{target_minor}"
