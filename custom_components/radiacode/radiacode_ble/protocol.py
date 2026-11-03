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
import math
import re
import struct
from dataclasses import dataclass, field
from enum import Enum, IntEnum
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
    WR_VIRT_STRING    = 0x0827  # write virtual string (spectrum reset)
    RD_VIRT_SFR_BATCH = 0x082A  # read multiple VSFRs in one command
    SET_TIME          = 0x0A04  # set device clock


# ── Virtual String IDs (args to RD_VIRT_STRING) ───────────────────────────────

class VS(IntEnum):
    CONFIGURATION = 0x02   # cp1251 text config (contains SpecFormatVersion)
    SERIAL_NUMBER = 0x08   # ASCII serial, e.g. "RC-103-012345"
    DATA_BUF      = 0x100  # real-time + accumulated sensor data stream
    SFR_FILE      = 0x101  # ASCII directory of all SFRs the device supports
    SPECTRUM      = 0x200  # current gamma spectrum (since last spectrum reset)
    ENERGY_CALIB  = 0x202  # 3 floats: channel→keV calibration coefficients
    SPEC_ACCUM    = 0x205  # accumulated gamma spectrum


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
    ACC_X          = 0x8025   # accelerometer X axis (int16, raw)
    ACC_Y          = 0x8026   # accelerometer Y axis (int16, raw)
    ACC_Z          = 0x8027   # accelerometer Z axis (int16, raw)

    # Detector / system health registers (read-only)
    VBIAS_mV       = 0xC000       # SiPM bias voltage, mV (uint16)
    SYS_MCU_VREF   = 0xFFFF000C   # MCU reference voltage, mV (int32)
    SYS_MCU_TEMP   = 0xFFFF000D   # MCU temperature, °C (int32)


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
    # Signed 16-bit values live in the low half of the uint32 register
    # ("h2x" = int16 + 2 pad bytes, little-endian).
    VSFR.ACC_X:          "h2x",
    VSFR.ACC_Y:          "h2x",
    VSFR.ACC_Z:          "h2x",
    VSFR.VBIAS_mV:       "I",
    VSFR.SYS_MCU_VREF:   "i",
    VSFR.SYS_MCU_TEMP:   "i",
}

# VSFR IDs batch-read for device-health diagnostics (order matches
# RadiaCodeDiagnostics).  Hardware-verified on RC-103 FW 4.14: VBIAS reads
# ~27 000 mV, SYS_MCU_TEMP is centi-°C, SYS_MCU_VREF ~2 095 mV.  ACC_X/Y/Z
# are rejected (marked invalid) over BLE and are deliberately excluded.
DIAGNOSTIC_VSFR_IDS: list[int] = [
    VSFR.VBIAS_mV,
    VSFR.SYS_MCU_TEMP,
    VSFR.SYS_MCU_VREF,
]

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
    flags: int = 0         # opaque device measurement flags
    real_time_flags: int = 0  # opaque real-time flags, retained for diagnostics


@dataclass
class DoseRateDB:
    """Dose-rate database record (data_buf gid=2, appears periodically)."""
    dt: datetime.datetime
    count: int             # total counts in measurement period
    count_rate: float      # counts per second (CPS)
    dose_rate: float       # dose rate (float, units vary by firmware)
    dose_rate_err: float   # error, percent
    flags: int = 0         # opaque device measurement flags


@dataclass
class RawData:
    """Raw radiation data (data_buf gid=1, appears periodically)."""
    dt: datetime.datetime
    count_rate: float      # counts per second (CPS)
    dose_rate: float       # dose rate (float, units vary by firmware)


@dataclass
class DoseCounterData:
    """Independent non-resettable dose counter (DATA_BUF gid=4).

    The validated declaration supplies the native micro-dose unit (legacy µR
    or µrem). It must not replace RareData's accumulated dose since reset.
    """
    dt: datetime.datetime
    dose_uR: int           # compatibility name; unsigned native micro-dose units
    flags: int = 0         # opaque device history flags


@dataclass
class RareData:
    """Periodic status snapshot (data_buf gid=3, appears ~every minute)."""
    dt: datetime.datetime
    dose: float            # accumulated dose since reset, declared R or rem
    charge_level: float    # battery, 0–100 percent
    temperature: float     # device temperature, °C
    flags: int = 0         # opaque device status flags


@dataclass(frozen=True)
class EventField:
    """An optional event channel validated against the device declaration."""
    channel_id: int
    fmt: str
    scale: float = 1.0
    offset: float = 0.0


@dataclass(frozen=True)
class EventSchema:
    """Ordered optional channels; fixed fields are event, mask and flags."""
    fields: tuple[EventField, ...]


@dataclass
class DeviceEvent:
    """Device event with opaque code/flags and values in declared native units."""
    dt: datetime.datetime
    code: int
    channel_mask: int
    flags: int
    values: dict[int, float]


@dataclass
class RadiaCodeData:
    """Aggregated sensor readings for one poll cycle.

    All dose values are already converted to µSv-based units by
    extract_sensor_values using the supported device dose conventions. New
    firmware declares rem/µrem; older firmware uses R/µR labels. The latter
    conversion is retained for compatibility, not a universal R/rem equivalence.
    """
    dose_rate: Optional[float]         # µSv/h, converted from native dose units
    count_rate: Optional[float]        # CPS      (paired with dose-rate sample)
    accumulated_dose: Optional[float]  # µSv, converted from native dose units
    battery: Optional[float]           # percent  (from RareData)
    temperature: Optional[float]       # °C       (from RareData)
    hardness: Optional[float] = None   # dimensionless (µR/h per cps), derived
    measurement_time: Optional[datetime.datetime] = None
    measurement_type: Optional[str] = None
    measurement_flags: Optional[int] = None
    dose_counter: Optional[float] = None  # independent non-resettable counter, µSv
    data_buf_status: dict = field(default_factory=dict)
    dose_rate_error_percent: Optional[float] = None
    count_rate_error_percent: Optional[float] = None
    real_time_flags: Optional[int] = None
    temperature_time: Optional[datetime.datetime] = None
    accumulated_dose_time: Optional[datetime.datetime] = None


def compute_hardness(
    dose_rate_uSv_h: Optional[float], count_rate_cps: Optional[float]
) -> Optional[float]:
    """Compute the radiation hardness coefficient shown by the Radiacode app.

    Hardness = dose rate (µR/h) ÷ count rate (cps) — a dimensionless
    measure of which energies dominate the spectrum, independent of
    intensity.  Each isotope has a characteristic hardness, so the app
    uses it for pseudo-identification of sources.

    Our dose_rate is stored in µSv/h; the device's fixed conversion is
    100 µR = 1 µSv, hence the ×100.

    Returns None when either input is missing or the count rate is zero
    (hardness is undefined without counts).
    """
    if dose_rate_uSv_h is None or count_rate_cps is None or count_rate_cps <= 0:
        return None
    return (dose_rate_uSv_h * 100.0) / count_rate_cps


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


@dataclass
class RadiaCodeDiagnostics:
    """Device-health readings, read from the diagnostics VSFR batch.

    Fields are in the same order as DIAGNOSTIC_VSFR_IDS.  Any register the
    device marks invalid (or rejects over BLE) stays None.
    """
    sipm_bias_mv: Optional[int] = None        # SiPM bias voltage, mV
    mcu_temperature: Optional[float] = None   # MCU temperature, °C
    mcu_vref_mv: Optional[int] = None         # MCU reference voltage, mV


def decode_diagnostics(values: list[int | float | None]) -> RadiaCodeDiagnostics:
    """Convert raw VSFR batch values (DIAGNOSTIC_VSFR_IDS order) to diagnostics.

    SYS_MCU_TEMP is reported in centi-degrees (°C × 100); verified on
    RC-103 FW 4.14 where raw 2971 corresponds to ~29.7 °C, a few degrees
    above the detector-board temperature as expected for the MCU.
    """
    return RadiaCodeDiagnostics(
        sipm_bias_mv=int(values[0]) if values[0] is not None else None,
        mcu_temperature=values[1] / 100.0 if values[1] is not None else None,
        mcu_vref_mv=int(values[2]) if values[2] is not None else None,
    )


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

    cmd_lo, cmd_hi, zero, seq_byte = struct.unpack_from("<BBBB", raw, 0)
    actual_cmd      = cmd_lo | (cmd_hi << 8)
    expected_seq_byte = 0x80 + (expected_seq % 32)

    if actual_cmd != expected_cmd or zero != 0 or seq_byte != expected_seq_byte:
        raise ValueError(
            f"Response echo header mismatch: "
            f"cmd={actual_cmd:#06x} (want {expected_cmd:#06x}), "
            f"reserved={zero:#04x} (want 0x00), "
            f"seq={seq_byte:#04x} (want {expected_seq_byte:#04x})"
        )

    return raw[4:]


def parse_vs_response(payload: bytes) -> bytes:
    """
    Parse the body of a RD_VIRT_STRING response into raw VS data bytes.

    After the 4-byte echo header (already stripped by parse_response_body):
      [uint32_le retcode] [uint32_le data_len] [data_len bytes]

    The transport supplies a complete outer frame. A shorter inner virtual
    string is malformed and must not be used as configuration or sensor data.
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

    if len(data) != data_len:
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
      [uint32_le retcode] [uint32_le raw_value, only on success]

    The raw uint32 value is reinterpreted through ``_VSFR_FORMATS``
    (e.g. TEMP_degC is stored as a float in the uint32 bit pattern).

    Raises ValueError if the response is too short or the device
    returned a non-success retcode.
    """
    if len(payload) < 4:
        raise ValueError(
            f"VSFR read response too short: {len(payload)} bytes, need status"
        )
    (retcode,) = struct.unpack_from("<I", payload, 0)
    if retcode != 1:
        raise ValueError(f"VSFR read failed: retcode={retcode}")
    if len(payload) != 8:
        raise ValueError(
            f"VSFR read response invalid length: {len(payload)} bytes, expected 8"
        )
    (raw,) = struct.unpack_from("<I", payload, 4)
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

class Group4Format(str, Enum):
    """Layouts positively identified in the detector configuration."""

    DOSE_COUNTER = "dose_counter"
    LEGACY_USER_DATA = "legacy_user_data"


def parse_group4_format(config_text: str) -> Optional[Group4Format]:
    """Identify group 4 from the actual VS_CONFIGURATION schema.

    The modern counter and older UserData share an ID but have different
    lengths and meanings. Accept only a complete recognized declaration;
    neither firmware version nor bytes remaining establish its layout.
    """
    groups = []
    in_data_structure = False
    group = None
    channel = None
    for raw_line in config_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        section = re.fullmatch(r"(\[+)([^\[\]]+)(\]+)", line)
        if section:
            depth = len(section[1])
            if depth != len(section[3]):
                if group is not None:
                    group["invalid"] = True
                continue
            name = section[2].strip()
            if depth == 1:
                in_data_structure = name == "DataStructure"
                group = None
                channel = None
            elif depth == 2 and in_data_structure:
                group = {"name": name, "id": None, "channels": [], "invalid": False}
                groups.append(group)
                channel = None
            elif depth == 3 and in_data_structure and group is not None:
                channel = {
                    "name": name, "id": None, "dtype": None,
                    "unit": None, "expr": None,
                }
                group["channels"].append(channel)
            elif group is not None:
                group["invalid"] = True
            continue
        if not in_data_structure or group is None:
            continue
        key, separator, value = line.partition("=")
        if not separator or key.strip() not in ("Id", "DType", "Unit", "Expr"):
            continue
        key = key.strip()
        if key == "Unit":
            if channel is not None:
                if channel["unit"] is not None:
                    group["invalid"] = True
                channel["unit"] = value.strip().strip('"').split("|", 1)[0].strip()
            continue
        target = channel if channel is not None else group
        field_name = {"Id": "id", "DType": "dtype", "Expr": "expr"}[key]
        if key in ("DType", "Expr") and channel is None:
            group["invalid"] = True
            continue
        if target.get(field_name) is not None:
            group["invalid"] = True
            continue
        try:
            target[field_name] = int(value.strip(), 0)
        except ValueError:
            group["invalid"] = True

    matching = [item for item in groups if item["id"] == 4]
    if len(matching) != 1:
        return None
    group = matching[0]
    channels = group["channels"]
    ids = [item["id"] for item in channels]
    if (
        group["invalid"]
        or any(value is None or not 0 <= value <= 255 for value in ids)
        or len(set(ids)) != len(ids)
    ):
        return None
    if (
        group["name"] == "GRP_DoseCounter"
        and [(item["name"], item["id"], item["dtype"]) for item in channels]
        == [("CHN_DoseCounter", 22, 8), ("CHN_Flags", 0, 4)]
        and channels[0]["unit"] in ("uR", "µR", "μR", "urem", "µrem", "μrem")
        # The app applies Expr=1 using P1/P2 before returning channel values.
        # Our supported counter and flags are raw integers without that scale.
        and all(item["expr"] in (None, 0) for item in channels)
    ):
        return Group4Format.DOSE_COUNTER
    if (
        group["name"] == "GRP_UserData"
        and [item["dtype"] for item in channels] == [8, 1, 1, 4, 4]
        and channels[-1]["name"] == "CHN_Flags"
        and channels[-1]["id"] == 0
    ):
        return Group4Format.LEGACY_USER_DATA
    return None


def describe_record_layout(config_text: str) -> list[dict]:
    """Bounded structural diagnostics, without arbitrary configuration text.

    These declarations describe evidence only; they never authorize decoding.
    Export numeric IDs/types/scales and known field names, excluding device
    identity, sensor descriptions, and all unrelated configuration sections.
    """
    known_groups = {"GRP_" + name for name in (
        "RealTimeData", "RawData", "DoseRateDB", "RareData", "DoseCounter",
        "UserData", "ScheduleData", "MeasData", "AccelData", "Event", "RawCountRate", "RawDoseRate",
    )}
    known_channels = {"CHN_" + name for name in (
        "CountRate", "DoseRate", "CountRateErr", "DoseRateErr", "Flags",
        "RealTimeFlags", "Count", "Duration", "Dose", "Temperature",
        "ChargeLevel", "DoseCounter", "Event", "EventChnMsk",
        "MeasTime", "MeasSID", "MeasCount", "MeasFlags",
    )}
    groups: list[dict] = []
    in_data = False
    group = channel = None
    for raw in config_text.splitlines():
        line = raw.strip()
        section = re.fullmatch(r"(\[+)([^\[\]]+)(\]+)", line)
        if section and len(section[1]) == len(section[3]):
            depth, name = len(section[1]), section[2].strip()
            if depth == 1:
                in_data = name == "DataStructure"
                group = channel = None
            elif in_data and depth == 2:
                if len(groups) >= 32:
                    break
                group = {"name": name if name in known_groups else "unrecognized", "channels": []}
                groups.append(group)
                channel = None
            elif in_data and depth == 3 and group is not None:
                if len(group["channels"]) >= 32:
                    break
                channel = {"name": name if name in known_channels else "unrecognized"}
                group["channels"].append(channel)
            continue
        if not in_data or group is None:
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        target = channel if channel is not None else group
        if key == "Unit" and channel is not None:
            unit = value.strip().strip('"').split("|", 1)[0].strip()
            if unit in {"uR", "µR", "μR", "urem", "µrem", "μrem", "R", "rem", "R/h", "rem/h", "%", "°C", "cps", "s"}:
                target[key] = unit
            continue
        if not sep or key not in {"Id", "DType", "RType", "Expr", "P1", "P2"}:
            continue
        try:
            number = float(value) if key in {"P1", "P2"} else int(value.strip(), 0)
            if math.isfinite(number) and abs(number) <= 2**32:
                target[key] = number
        except (ValueError, OverflowError):
            continue
    return groups


@dataclass(frozen=True)
class ScalarField:
    """A validated 1XX scalar channel, in its declared wire order."""

    channel_id: int
    fmt: str
    scale: float
    offset: float
    unit: Optional[str] = None


@dataclass(frozen=True)
class ScalarSchema:
    fields: tuple[ScalarField, ...]

    def read(self, buf: _Buf) -> dict[int, float | int]:
        values = {}
        for field in self.fields:
            (value,) = buf.unpack("<" + field.fmt)
            values[field.channel_id] = (
                value if (field.scale, field.offset) == (1, 0)
                else value * field.scale + field.offset
            )
        return values


def parse_scalar_schemas(config_text: str) -> dict[int, Optional[ScalarSchema]]:
    """Read supported scalar groups without guessing widths from firmware.

    An explicitly unsupported declaration blocks that group. Undeclared groups
    retain the documented legacy fallback. Malformed structure blocks all supported
    groups. Limits apply before parsing, including to unrelated declarations.
    RType describes the represented value; DType determines the wire width.
    """
    supported_groups = {0, 1, 2, 3, 5}
    blocked = dict.fromkeys(sorted(supported_groups))
    if isinstance(config_text, str) and not config_text.strip():
        return {}  # Older devices may return no declarations at all.
    if not isinstance(config_text, str) or not 0 < len(config_text) <= 65536:
        return blocked
    groups = []
    group = channel = None
    in_data = False
    data_sections = 0
    try:
        for raw in config_text.splitlines():
            if len(raw) > 2048:
                return blocked
            line = raw.strip()
            if not line or line.startswith((";", "#")):
                continue
            section = re.fullmatch(r"(\[+)([^\[\]]+)(\]+)", line)
            if section:
                if len(section[1]) != len(section[3]):
                    return blocked
                depth, name = len(section[1]), section[2].strip()
                if depth == 1:
                    in_data = name == "DataStructure"
                    data_sections += int(in_data)
                    group = channel = None
                elif in_data and depth == 2:
                    if len(groups) >= 32:
                        return blocked
                    group = {"name": name, "channels": []}
                    groups.append(group)
                    channel = None
                elif in_data and depth == 3 and group is not None:
                    if len(group["channels"]) >= 32:
                        return blocked
                    channel = {"name": name}
                    group["channels"].append(channel)
                elif in_data:
                    return blocked
                continue
            if line.startswith("["):
                return blocked
            if not in_data or group is None:
                continue
            key, sep, value = line.partition("=")
            target = channel if channel is not None else group
            key = key.strip()
            if not sep or key in target:
                return blocked
            target[key] = value.strip()
        group_ids = [int(g["Id"], 0) for g in groups]
        if data_sections != 1:
            return blocked if any(g in supported_groups for g in group_ids) else {}
        if len(set(group_ids)) != len(group_ids) or any(not 0 <= g <= 255 for g in group_ids):
            return blocked if any(g in supported_groups for g in group_ids) else {}
        # name, ID, wire types, representation, scale, offset, unit
        known = {
            "CHN_CountRate": (3, {1: "f"}, 1, 1, 0, "cps"),
            "CHN_DoseRate": (4, {1: "f"}, 1, 1, 0, ("R/h", "rem/h")),
            "CHN_CountRateErr": (16, {4: "H"}, 1, .1, 0, "%"),
            "CHN_DoseRateErr": (5, {4: "H"}, 1, .1, 0, "%"),
            "CHN_Flags": (0, {4: "H"}, 8, 1, 0, None),
            "CHN_RealTimeFlags": (17, {2: "B", 4: "H"}, 8, 1, 0, None),
            "CHN_Count": (2, {8: "I"}, 8, 1, 0, None),
            "CHN_Duration": (6, {8: "I"}, 8, 1, 0, "s"),
            "CHN_Dose": (7, {1: "f"}, 1, 1, 0, ("R", "rem")),
            "CHN_Temperature": (8, {5: "h"}, 1, .01, -20, "°C"),
            "CHN_ChargeLevel": (9, {4: "H"}, 1, .01, 0, "%"),
            "CHN_MeasTime": (23, {4: "H"}, 4, 1, 0, "s"),
            "CHN_MeasSID": (24, {2: "B"}, 2, 1, 0, None),
            "CHN_MeasCount": (25, {2: "B"}, 2, 1, 0, None),
            "CHN_MeasFlags": (26, {2: "B"}, 2, 1, 0, None),
        }
        expected = {
            0: ("GRP_RealTimeData", {3, 4, 16, 5, 0, 17}),
            1: ("GRP_RawData", {3, 4}),
            2: ("GRP_DoseRateDB", {2, 3, 4, 5, 0}),
            3: ("GRP_RareData", {6, 7, 8, 9, 0}),
            5: ("GRP_MeasData", {3, 4, 16, 5, 23, 24, 25, 26, 0}),
        }
        result = {}
        for group, gid in zip(groups, group_ids):
            if gid not in expected:
                # A known name under an unexpected ID is contradictory.
                if group["name"] in {item[0] for item in expected.values()}:
                    return blocked
                continue
            result[gid] = None
            name, ids = expected[gid]
            if gid == 5 and group["name"] == "GRP_ScheduleData":
                name, ids = "GRP_ScheduleData", expected[2][1]
            if group["name"] != name:
                continue
            fields = []
            try:
                for ch in group["channels"]:
                    cid, types, rtype, scale, offset, unit = known[ch["name"]]
                    fmt = types[int(ch["DType"], 0)]
                    if int(ch["Id"], 0) != cid or int(ch["RType"], 0) != rtype:
                        raise ValueError("Unsupported channel")
                    declared_unit = ch.get("Unit", "").strip('"').split("|", 1)[0].strip()
                    allowed_units = unit if isinstance(unit, tuple) else (unit,)
                    if unit is not None and declared_unit not in allowed_units:
                        raise ValueError("Unsupported unit")
                    expr = int(ch.get("Expr", "0"), 0)
                    if (scale, offset) == (1, 0):
                        if expr != 0 or "P1" in ch or "P2" in ch:
                            raise ValueError("Unexpected expression")
                    elif (expr, float(ch["P1"]), float(ch.get("P2", "0"))) != (1, scale, offset):
                        raise ValueError("Unsupported expression")
                    fields.append(ScalarField(cid, fmt, scale, offset, declared_unit or None))
                if len(fields) == len(ids) and {f.channel_id for f in fields} == ids:
                    result[gid] = ScalarSchema(tuple(fields))
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
        return result
    except (KeyError, ValueError, TypeError, OverflowError):
        return blocked


def parse_event_schema(config_text: str) -> Optional[EventSchema]:
    """Validate the declared 1XX event layout, never infer it from packet size.

    Only the documented fixed prefix, mask expression and known channel
    types/scales are accepted. Optional channel order comes from the device.
    Duplicate keys/groups, unknown fields/types and missing values fail closed.
    """
    groups: list[dict] = []
    group = channel = None
    in_data = False
    data_sections = 0
    try:
        for raw in config_text.splitlines():
            line = raw.strip()
            if not line or line.startswith((";", "#")):
                continue
            section = re.fullmatch(r"(\[+)([^\[\]]+)(\]+)", line)
            if section:
                if len(section[1]) != len(section[3]):
                    return None
                depth, name = len(section[1]), section[2].strip()
                if depth == 1:
                    in_data = name == "DataStructure"
                    data_sections += int(in_data)
                    group = channel = None
                elif in_data and depth == 2:
                    group = {"name": name, "channels": []}
                    groups.append(group)
                    channel = None
                elif in_data and depth == 3 and group is not None:
                    channel = {"name": name}
                    group["channels"].append(channel)
                elif in_data:
                    return None
                continue
            if line.startswith("["):
                return None
            if not in_data or group is None:
                continue
            key, sep, value = line.partition("=")
            if not sep:
                return None
            target = channel if channel is not None else group
            key = key.strip()
            if key in target:
                return None
            target[key] = value.strip()
        matching = [g for g in groups if int(g.get("Id", "-1"), 0) == 7]
        if data_sections != 1 or len(matching) != 1:
            return None
        group = matching[0]
        channels = group["channels"]
        if group["name"] != "GRP_Event" or not 3 <= len(channels) <= 11:
            return None
        expected_prefix = [("CHN_Event", 14, 2), ("CHN_EventChnMsk", 15, 2), ("CHN_Flags", 0, 4)]
        for ch, expected in zip(channels, expected_prefix):
            if (ch["name"], int(ch["Id"], 0), int(ch["DType"], 0)) != expected:
                return None
        for ch in (channels[0], channels[2]):
            if int(ch.get("Expr", "0"), 0) != 0 or "P1" in ch or "P2" in ch:
                return None
        mask = channels[1]
        if (int(mask["Expr"], 0), float(mask["P1"]), float(mask["P2"])) != (1, 8.0, 7.0):
            return None
        known = {
            "CHN_CountRate": (3, 1, "f", 1.0, 0.0),
            "CHN_CountRateErr": (16, 4, "H", 0.1, 0.0),
            "CHN_DoseRate": (4, 1, "f", 1.0, 0.0),
            "CHN_DoseRateErr": (5, 4, "H", 0.1, 0.0),
            "CHN_Duration": (6, 8, "I", 1.0, 0.0),
            "CHN_Dose": (7, 1, "f", 1.0, 0.0),
            "CHN_Temperature": (8, 5, "h", 0.01, -20.0),
            "CHN_ChargeLevel": (9, 4, "H", 0.01, 0.0),
        }
        fields = []
        seen = {0, 14, 15}
        for ch in channels[3:]:
            channel_id, dtype, fmt, scale, offset = known[ch["name"]]
            if int(ch["Id"], 0) != channel_id or int(ch["DType"], 0) != dtype or channel_id in seen:
                return None
            expr = int(ch.get("Expr", "0"), 0)
            if (scale, offset) == (1.0, 0.0):
                if expr != 0 or "P1" in ch or "P2" in ch:
                    return None
            elif (expr, float(ch["P1"]), float(ch.get("P2", "0"))) != (1, scale, offset):
                return None
            seen.add(channel_id)
            fields.append(EventField(channel_id, fmt, scale, offset))
        return EventSchema(tuple(fields))
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def decode_data_buf(
    data: bytes, base_time: datetime.datetime, diagnostics: Optional[dict] = None,
    *, group4_format: Optional[Group4Format] = None,
    event_schema: Optional[EventSchema] = None,
    event_schema_required: bool = False,
    scalar_schemas: Optional[dict[int, Optional[ScalarSchema]]] = None,
) -> list:
    """
    Decode the raw DATA_BUF byte stream into a list of typed records.

    Each record starts with a 7-byte header:
      [uint8 seq] [uint8 eid] [uint8 gid] [int32_le ts_offset_10ms]

    Timestamps are base_time + ts_offset * 10 ms.
    Decoding stops on unknown record types or sequence-number jumps. Optional
    diagnostics describe where decoding stopped without retaining raw bytes.
    Group 4 requires a recognized layout from the actual device configuration.
    """
    buf = _Buf(data)
    records: list = []
    next_seq: Optional[int] = None

    # Diagnostic counters for record types
    gid_counts: dict[int, int] = {}
    record_group_counts: dict[str, int] = {}

    stop_reason = "complete"
    stop_offset = len(data)

    while buf.remaining() >= 7:
        record_offset = len(data) - buf.remaining()
        seq, eid, gid, ts_offset = buf.unpack("<BBBi")
        dt = base_time + datetime.timedelta(milliseconds=ts_offset * 10)

        if next_seq is not None and next_seq != seq:
            stop_reason = f"sequence_jump(expected={next_seq},received={seq})"
            stop_offset = record_offset
            break  # sequence jump — stop decoding

        next_seq = (seq + 1) % 256
        gid_counts[gid] = gid_counts.get(gid, 0) + 1
        group_key = f"{eid}:{gid}"
        record_group_counts[group_key] = record_group_counts.get(group_key, 0) + 1

        try:
            if eid == 0 and scalar_schemas is not None and gid in scalar_schemas:
                schema = scalar_schemas[gid]
                if schema is None:
                    stop_reason = f"unsupported_scalar_schema(group={gid})"
                    stop_offset = record_offset
                    break
                values = schema.read(buf)
                if gid == 0:
                    records.append(RealTimeData(dt, values[3], values[16], values[4], values[5], values[0], values[17]))
                elif gid == 1:
                    records.append(RawData(dt, values[3], values[4]))
                elif gid == 2:
                    records.append(DoseRateDB(dt, values[2], values[3], values[4], values[5], values[0]))
                elif gid == 3:
                    records.append(RareData(dt, values[7], values[9], values[8], values[0]))
                # Group 5 is consumed in its validated declared order. Session
                # measurements are not live samples and cannot renew freshness.
                continue
            if eid == 0 and gid == 0:       # GRP_RealTimeData
                count_rate, dose_rate, cps_err, dr_err, flags, rt_flags = buf.unpack("<ffHHHB")
                records.append(RealTimeData(
                    dt=dt,
                    count_rate=count_rate,
                    count_rate_err=cps_err / 10,
                    dose_rate=dose_rate,
                    dose_rate_err=dr_err / 10,
                    flags=flags,
                    real_time_flags=rt_flags,
                ))

            elif eid == 0 and gid == 1:     # GRP_RawData
                count_rate, dose_rate = buf.unpack("<ff")
                records.append(RawData(
                    dt=dt,
                    count_rate=count_rate,
                    dose_rate=dose_rate,
                ))

            elif eid == 0 and gid == 2:     # GRP_DoseRateDB
                count, count_rate, dose_rate, dr_err, flags = buf.unpack("<IffHH")
                records.append(DoseRateDB(
                    dt=dt,
                    count=count,
                    count_rate=count_rate,
                    dose_rate=dose_rate,
                    dose_rate_err=dr_err / 10,
                    flags=flags,
                ))

            elif eid == 0 and gid == 3:     # GRP_RareData
                _dur, dose, temperature, charge_level, flags = buf.unpack("<IfhHH")
                records.append(RareData(
                    dt=dt,
                    dose=dose,
                    charge_level=charge_level / 100,         # → 0–100 percent
                    temperature=(temperature - 2000) / 100,  # → °C
                    flags=flags,
                ))

            elif eid == 0 and gid == 4:
                if group4_format == Group4Format.DOSE_COUNTER:
                    dose_uR, flags = buf.unpack("<IH")
                    records.append(DoseCounterData(dt=dt, dose_uR=dose_uR, flags=flags))
                elif group4_format == Group4Format.LEGACY_USER_DATA:
                    buf.unpack("<IffHH")
                else:
                    stop_reason = "unsupported_group4_schema"
                    stop_offset = record_offset
                    break

            elif eid == 0 and gid == 5:     # GRP_ScheduleData (skip)
                buf.unpack("<IffHH")

            elif eid == 0 and gid == 6:     # GRP_AccelData (skip)
                buf.unpack("<HHH")

            elif eid == 0 and gid == 7:     # GRP_Event (skip)
                event_code, channel_mask, flags = buf.unpack("<BBH")
                if event_schema is not None:
                    if channel_mask >> len(event_schema.fields):
                        stop_reason = f"unsupported_event_mask(mask=0x{channel_mask:02x})"
                        stop_offset = record_offset
                        break
                    values = {}
                    for bit, channel in enumerate(event_schema.fields):
                        if channel_mask & (1 << bit):
                            (value,) = buf.unpack("<" + channel.fmt)
                            values[channel.channel_id] = value * channel.scale + channel.offset
                    records.append(DeviceEvent(dt, event_code, channel_mask, flags, values))
                    continue
                if event_schema_required:
                    stop_reason = "unsupported_event_schema"
                    stop_offset = record_offset
                    break
                # A captured count alarm (mask 3) and documented dose alarms
                # (mask 12) carry a float reading plus a uint16 error after
                # the fixed fields. See cdump/radiacode issue #45. Other mask
                # layouts remain unverified; never guess their byte lengths.
                if channel_mask not in (0, 3, 12):
                    _LOGGER.debug(
                        "DATA_BUF Event: code=%d mask=0x%02x flags=0x%04x "
                        "payload_bytes=unknown outcome=unsupported_mask",
                        event_code, channel_mask, flags,
                    )
                    stop_reason = (
                        f"unsupported_event_mask(event={event_code},"
                        f"mask=0x{channel_mask:02x},flags=0x{flags:04x})"
                    )
                    stop_offset = record_offset
                    break
                payload_bytes = 4 if channel_mask == 0 else 10
                _LOGGER.debug(
                    "DATA_BUF Event: code=%d mask=0x%02x flags=0x%04x "
                    "payload_bytes=%d record_bytes=%d",
                    event_code, channel_mask, flags, payload_bytes,
                    7 + payload_bytes,
                )
                if channel_mask in (3, 12):
                    buf.unpack("<fH")

            elif eid == 0 and gid == 8:     # GRP_RawCountRate (skip)
                buf.unpack("<fH")

            elif eid == 0 and gid == 9:     # GRP_RawDoseRate (skip)
                buf.unpack("<fH")

            elif eid == 1 and gid in (1, 2, 3):  # variable-length sample blocks
                (samples_num,) = buf.unpack("<H")
                buf.unpack("<I")   # smpl_time_ms
                # A declared block larger than the remaining payload is an
                # incomplete record, even when no scalar sensor is emitted.
                buf.unpack(f"<{samples_num * _SAMPLE_SIZES[gid]}x")

            else:
                stop_reason = f"unknown_record(eid={eid},gid={gid})"
                stop_offset = record_offset
                break  # unknown record type; stop rather than misparse

        except ValueError as err:
            stop_reason = f"incomplete_record(eid={eid},gid={gid},error={err})"
            stop_offset = record_offset
            break  # truncated record; stop cleanly

    if stop_reason == "complete" and buf.remaining():
        stop_reason = "incomplete_header"
        stop_offset = len(data) - buf.remaining()
    if diagnostics is not None:
        diagnostics.update({
            "payload_bytes": len(data),
            "decoded_records": len(records),
            "record_group_counts": record_group_counts,
            "record_types": {
                record_type: sum(type(record).__name__ == record_type for record in records)
                for record_type in sorted({type(record).__name__ for record in records})
            },
            "stop_reason": stop_reason,
            "stop_offset": stop_offset,
            "unread_bytes": len(data) - stop_offset,
        })
    if stop_reason != "complete" and _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug(
            "DATA_BUF decode stopped: reason=%s offset=%d unread_bytes=%d prefix=%s",
            stop_reason, stop_offset, len(data) - stop_offset,
            data[stop_offset:stop_offset + 32].hex(),
        )
    # Log record type distribution and dose_rate values for diagnostics.
    # Log the declared native unit; absent declarations retain the legacy label.
    # Conversion factors and record values are unchanged by these debug labels.
    dose_units = {}
    for gid in (0, 1, 2):
        schema = (scalar_schemas or {}).get(gid)
        dose_units[gid] = next(
            (field.unit for field in schema.fields if field.channel_id == 4),
            "R/h",
        ) if schema is not None else "R/h"
    _LOGGER.debug(
        "decode_data_buf: bytes=%d gid_counts=%s total_records=%d stop_reason=%s",
        len(data), gid_counts, len(records), stop_reason,
    )
    for r in records:
        if isinstance(r, RealTimeData):
            _LOGGER.debug(
                "  RealTimeData: count_rate=%.2f dose_rate=%.6e %s (%.4f µSv/h) err=%.1f%%",
                r.count_rate, r.dose_rate, dose_units[0], r.dose_rate * 10_000, r.dose_rate_err,
            )
        elif isinstance(r, DoseRateDB):
            _LOGGER.debug(
                "  DoseRateDB: count=%d count_rate=%.2f dose_rate=%.6e %s (%.4f µSv/h) err=%.1f%%",
                r.count, r.count_rate, r.dose_rate, dose_units[2], r.dose_rate * 10_000, r.dose_rate_err,
            )
        elif isinstance(r, RawData):
            _LOGGER.debug(
                "  RawData: count_rate=%.2f dose_rate=%.6e %s (%.4f µSv/h)",
                r.count_rate, r.dose_rate, dose_units[1], r.dose_rate * 10_000,
            )

    return records


def _valid_reading(value: Optional[float], minimum: float = 0.0) -> bool:
    """True when *value* is a finite number not below *minimum*.

    data_buf transfers through ESPHome BT proxies are frequently truncated
    mid-stream; a rare mid-stream packet loss can misalign record parsing
    and produce garbage floats (NaN, inf, negatives, or absurd magnitudes).
    Finite/non-negative is the record-level sanity gate; magnitude outliers
    are handled statistically by SpikeFilter in the coordinator.
    """
    return value is not None and math.isfinite(value) and value >= minimum


class SpikeFilter:
    """Suppress single-sample spikes without hiding genuine radiation events.

    A reading is passed through immediately unless it exceeds the last
    accepted reading by more than *factor*.  Such a suspect reading is held
    back for one sample; if the next reading is consistent with it (within
    *factor* either way), the event is treated as genuine and passed —
    real radiation excursions are sustained across polls, while corrupt
    values from misparsed BLE data are one-off and wildly inconsistent.

    Readings at or below *pass_below* are never filtered, so normal
    background fluctuation (which can easily be a large *ratio* while
    remaining a tiny absolute value) is unaffected.

    filter() returns the accepted value, or None while a suspect reading
    awaits confirmation (callers keep their previous value for that sample).
    """

    def __init__(self, factor: float = 50.0, pass_below: float = 5.0) -> None:
        self._factor = factor
        self._pass_below = pass_below
        self._baseline: Optional[float] = None
        self._pending: Optional[float] = None
        # Set after each filter() call: the suppressed value, or None.
        self.last_suppressed: Optional[float] = None

    def filter(self, value: Optional[float]) -> Optional[float]:
        """Return *value* if accepted, else None (held for confirmation)."""
        self.last_suppressed = None
        if value is None:
            return None
        if not math.isfinite(value) or value < 0:
            self.last_suppressed = value
            return None

        if (
            value <= self._pass_below
            or self._baseline is None
            or value <= self._baseline * self._factor
        ):
            self._baseline = value
            self._pending = None
            return value

        # Suspect spike.  Accept only if consistent with the held sample.
        if (
            self._pending is not None
            and self._pending > 0
            and value <= self._pending * self._factor
            and value >= self._pending / self._factor
        ):
            self._baseline = value
            self._pending = None
            return value

        self._pending = value
        self.last_suppressed = value
        return None


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

    Dose rate and count rate always come from the same valid measurement,
    including zero values. Prefer the newest valid RealTimeData record for
    live sensors: RawData and DoseRateDB timestamps can lead the live stream
    by tens of seconds and must not displace it. When no valid live pair is
    present, retain the newest RawData/DoseRateDB fallback, preferring RawData
    on equal timestamps; equal type/time uses the last received record.
    RareData supplies the accumulated dose since reset, battery and temperature.
    DoseCounterData supplies a separate non-resettable history counter; it must
    not replace accumulated_dose or act as a live radiation measurement.

    The selected timestamp and record type are retained for diagnostics;
    timestamps are device offsets anchored at connection initialization,
    not independently verified wall-clock freshness measurements.
    """
    # rem/h -> µSv/h and rem -> µSv. Preserve the established conversion for
    # older device R labels; this is not a general exposure-to-dose conversion.
    _DOSE_TO_uSv = 10_000

    dose_rate: Optional[float] = None
    count_rate: Optional[float] = None
    accumulated_dose: Optional[float] = None
    battery: Optional[float] = None
    temperature: Optional[float] = None
    dose_counter: Optional[float] = None
    dose_counter_time: Optional[datetime.datetime] = None
    rare_time: Optional[datetime.datetime] = None
    events: list[dict] = []
    measurement: Optional[RealTimeData | RawData | DoseRateDB] = None
    measurement_key = None
    priorities = {RealTimeData: 2, RawData: 1, DoseRateDB: 0}
    candidates: dict[str, dict] = {}

    for r in records:
        if isinstance(r, (RealTimeData, DoseRateDB, RawData)):
            candidate = candidates.setdefault(type(r).__name__, {
                "valid_pairs": 0, "invalid_pairs": 0,
                "earliest_time": None, "latest_time": None,
            })
            if not (_valid_reading(r.dose_rate) and _valid_reading(r.count_rate)):
                candidate["invalid_pairs"] += 1
                _LOGGER.debug("Skipping implausible %s record: %s", type(r).__name__, r)
                continue
            candidate["valid_pairs"] += 1
            timestamp = r.dt.isoformat()
            candidate["earliest_time"] = min(
                candidate["earliest_time"] or timestamp, timestamp
            )
            candidate["latest_time"] = max(
                candidate["latest_time"] or timestamp, timestamp
            )
            # Rank live records before comparing timestamps: the observed
            # raw/database streams do not share a comparable live watermark.
            key = (isinstance(r, RealTimeData), r.dt, priorities[type(r)])
            if measurement_key is None or key >= measurement_key:
                measurement = r
                measurement_key = key
        elif isinstance(r, RareData):
            if (
                not _valid_reading(r.dose)
                or not _valid_reading(r.charge_level)
                or r.charge_level > 150
                or not _valid_reading(r.temperature, minimum=-100.0)
            ):
                _LOGGER.debug("Skipping implausible RareData record: %s", r)
                continue
            if rare_time is None or r.dt >= rare_time:
                accumulated_dose = r.dose * _DOSE_TO_uSv
                battery = r.charge_level           # already 0–100 percent
                temperature = r.temperature      # °C, already converted in decoder
                rare_time = r.dt
        elif isinstance(r, DoseCounterData):
            if not isinstance(r.dose_uR, int) or not 0 <= r.dose_uR <= 0xFFFFFFFF:
                _LOGGER.debug("Skipping implausible DoseCounterData record: %s", r)
                continue
            if dose_counter_time is None or r.dt >= dose_counter_time:
                dose_counter = r.dose_uR / 100.0  # native micro-dose -> µSv
                dose_counter_time = r.dt
        elif isinstance(r, DeviceEvent):
            events.append({
                "device_time": r.dt.isoformat(), "code": r.code,
                "channel_mask": r.channel_mask, "flags": r.flags,
                # Preserve native-unit values without asserting alarm meanings.
                "channels": {str(k): v for k, v in r.values.items() if math.isfinite(v)},
            })
            events = events[-5:]

    if measurement is not None:
        dose_rate = measurement.dose_rate * _DOSE_TO_uSv
        count_rate = measurement.count_rate

    return RadiaCodeData(
        dose_rate=dose_rate,
        count_rate=count_rate,
        accumulated_dose=accumulated_dose,
        battery=battery,
        temperature=temperature,
        measurement_time=measurement.dt if measurement is not None else None,
        measurement_type=type(measurement).__name__ if measurement is not None else None,
        measurement_flags=getattr(measurement, "flags", None),
        dose_rate_error_percent=_measurement_error(getattr(measurement, "dose_rate_err", None)),
        count_rate_error_percent=_measurement_error(getattr(measurement, "count_rate_err", None)),
        real_time_flags=getattr(measurement, "real_time_flags", None),
        temperature_time=rare_time,
        accumulated_dose_time=rare_time,
        dose_counter=dose_counter,
        data_buf_status={"measurement_candidates": candidates, "device_events": events},
    )


def _measurement_error(value: Optional[float]) -> Optional[float]:
    """A missing/invalid uncertainty does not invalidate a finite measurement."""
    return value if value is not None and math.isfinite(value) and value >= 0 else None


def measurement_quality(
    error_percent: Optional[float], limit: float, temperature: Optional[float],
) -> dict:
    """Calculated 1XX quality; firmware alarm/acknowledgment bits stay opaque.

    Temperature must already have passed the caller's freshness check. At
    exactly 45 C the manufacturer manual and release notes differ, so do not
    infer normal quality. Missing evidence never establishes normal quality.
    """
    reasons = []
    reduced = False
    error = _measurement_error(error_percent)
    if error is None:
        reasons.append("uncertainty_unknown")
    elif error > limit:
        reasons.append("statistical_uncertainty")
        reduced = True
    if temperature is None or not math.isfinite(temperature):
        reasons.append("temperature_unknown")
    elif temperature > 45:
        reasons.append("high_temperature")
        reduced = True
    elif temperature == 45:
        reasons.append("temperature_boundary")
    elif temperature < -20:
        reasons.append("low_temperature")
    return {
        "quality": "reduced" if reduced else "unknown" if reasons else "normal",
        "quality_reasons": reasons,
        "quality_source": "calculated",
    }


# ── Gamma spectrum ────────────────────────────────────────────────────────────

# Firmware 4.00 and newer use 1024 channels for the 10x / 110 models.
# https://radiacode.com/changelog?lang=en
SPECTRUM_CHANNELS = 1024


@dataclass
class Spectrum:
    """A gamma spectrum snapshot.

    Channel energies follow the quadratic calibration:
      E(ch) [keV] = a0 + a1·ch + a2·ch²
    """
    duration_s: int        # accumulation time, seconds
    a0: float              # calibration: constant term, keV
    a1: float              # calibration: linear term, keV/channel
    a2: float              # calibration: quadratic term, keV/channel²
    counts: list[int] = field(default_factory=list)
    truncated: bool = False  # True when fewer than all 1024 channels arrived


def channel_to_kev(channel: int, a0: float, a1: float, a2: float) -> float:
    """Convert a spectrum channel index to energy in keV."""
    return a0 + a1 * channel + a2 * channel * channel


def parse_spec_format_version(config_text: str) -> int:
    """Extract SpecFormatVersion from the device configuration text.

    Falls back to 0 when the line is absent or malformed, matching the
    upstream cdump/radiacode client. Firmware version alone does not
    select the spectrum encoding.
    """
    for line in config_text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "SpecFormatVersion":
            try:
                return int(value.strip())
            except ValueError:
                break
    return 0


def decode_spectrum(data: bytes, format_version: int) -> Spectrum:
    """Decode a VS.SPECTRUM / VS.SPEC_ACCUM payload.

    Header: [uint32 duration_s] [float a0] [float a1] [float a2],
    followed by per-channel counts:
      format 0 — one uint32 per channel
      format 1 — run-length groups: [uint16: count<<4 | vlen] then
                 `count` values encoded per vlen (0=zero, 1=uint8,
                 2/3=delta int8/int16, 4=delta int24, 5=absolute uint32)
                 A zero-count group is a no-op and consumes its two-byte header.

    Signed deltas wrap at 32 bits, matching device count arithmetic. The
    vlen=4 encoding has no padding. These rare branches follow the maintained
    BecqMoni decoder; older cdump-derived implementations use a signed delta
    for vlen=5 and can decode large absolute counts incorrectly. An incomplete
    transfer retains complete leading channels and is flagged
    ``truncated``, including a cut exactly between groups or values.

    Raises ValueError for an incomplete header, unknown format, invalid
    groups, non-finite calibration, or impossible channel counts.
    """
    if format_version not in (0, 1):
        raise ValueError(f"Unsupported spectrum format version: {format_version}")

    buf = _Buf(data)
    duration_s, a0, a1, a2 = buf.unpack("<Ifff")
    if not all(math.isfinite(value) for value in (a0, a1, a2)):
        raise ValueError("Spectrum calibration coefficients must be finite")

    counts: list[int] = []
    truncated = False

    if format_version == 0:
        if buf.remaining() > SPECTRUM_CHANNELS * 4:
            raise ValueError("Spectrum contains more than 1024 channels")
        while buf.remaining() >= 4:
            counts.append(buf.unpack("<I")[0])
        truncated = buf.remaining() > 0
    else:
        last = 0
        value_sizes = (0, 1, 1, 2, 3, 4)
        while buf.remaining() > 0:
            if buf.remaining() < 2:
                if len(counts) == SPECTRUM_CHANNELS:
                    raise ValueError("Spectrum contains trailing data after 1024 channels")
                truncated = True
                break
            (group,) = buf.unpack("<H")
            n_values = group >> 4
            vlen = group & 0x0F
            if vlen > 5:
                raise ValueError(f"Unsupported spectrum value encoding: vlen={vlen}")
            # Upstream cdump and BecqMoni consume empty groups normally. Each
            # header advances two bytes, even when no channels are produced;
            # empty groups do not alter the previous value used by deltas.
            if len(counts) + n_values > SPECTRUM_CHANNELS:
                raise ValueError("Spectrum contains more than 1024 channels")
            for _ in range(n_values):
                if buf.remaining() < value_sizes[vlen]:
                    truncated = True
                    break
                if vlen == 0:
                    v = 0
                elif vlen == 1:
                    (v,) = buf.unpack("<B")
                elif vlen == 2:
                    v = (last + buf.unpack("<b")[0]) & 0xFFFFFFFF
                elif vlen == 3:
                    v = (last + buf.unpack("<h")[0]) & 0xFFFFFFFF
                elif vlen == 4:
                    lo, mid, hi = buf.unpack("<BBb")
                    v = (last + ((hi << 16) | (mid << 8) | lo)) & 0xFFFFFFFF
                else:
                    v = buf.unpack("<I")[0]
                last = v
                counts.append(v)
            if truncated:
                break

    truncated = truncated or len(counts) < SPECTRUM_CHANNELS

    return Spectrum(
        duration_s=duration_s,
        a0=a0,
        a1=a1,
        a2=a2,
        counts=counts,
        truncated=truncated,
    )


def detect_spectrum_format(data: bytes) -> tuple[int, Spectrum]:
    """Infer an encoding only from a complete, uniquely valid spectrum.

    Callers must first validate the complete transport frame and virtual
    string length. Both supported decoders must consume the entire payload
    and produce exactly 1024 channels. Payload length alone cannot identify
    a format: a compressed spectrum can have the same size as a raw spectrum.
    Ambiguous data must use an authoritative format or be rejected, never
    guessed. The inferred encoding is not a firmware-version declaration.
    """
    candidates: list[tuple[int, Spectrum]] = []
    errors: list[str] = []
    for version in (0, 1):
        try:
            spectrum = decode_spectrum(data, version)
        except ValueError as err:
            errors.append(f"format {version}: {err}")
            continue
        if spectrum.truncated or len(spectrum.counts) != SPECTRUM_CHANNELS:
            errors.append(f"format {version}: incomplete ({len(spectrum.counts)} channels)")
            continue
        candidates.append((version, spectrum))

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValueError("Ambiguous spectrum encoding: both formats decode 1024 channels")
    raise ValueError("No complete supported spectrum encoding: " + "; ".join(errors))


# ── Serial number decoder ─────────────────────────────────────────────────────

def decode_serial_number(data: bytes) -> str:
    """Decode VS.SERIAL_NUMBER bytes into a string, e.g. 'RC-103-012345'."""
    return data.decode("ascii").strip("\x00")


# ── SFR directory decoder ─────────────────────────────────────────────────────

def decode_sfr_file(data: bytes) -> str:
    """Decode the VS.SFR_FILE payload into readable text.

    The device self-describes its Special Function Registers: the payload
    is an ASCII listing of every SFR with its address (opcode), size in
    bytes, type (int or float), and signedness.  Invaluable for verifying
    register formats per firmware version without reverse engineering.

    Decoded permissively (errors="replace") — through a BT proxy the
    transfer may be truncated mid-character, and a best-effort partial
    listing is far more useful than an exception.
    """
    return data.decode("ascii", errors="replace").strip("\x00").strip()


# ── Firmware version decoder ─────────────────────────────────────────────────

def parse_firmware_build(data: bytes) -> Optional[str]:
    """Read the application build from a complete two-tuple GET_VERSION reply.

    The bootloader build is never used as the application identity. Reject
    truncation, trailing bytes, embedded NULs and malformed dates. A known
    version without a complete build cannot authorize a bundled profile.
    """
    offset = 0
    build = None
    for _ in range(2):
        if len(data) < offset + 5:
            return None
        length = data[offset + 4]
        offset += 5
        if not 0 < length <= 64 or len(data) < offset + length:
            return None
        value = data[offset:offset + length]
        offset += length
        if value.endswith(b"\0"):
            value = value[:-1]
        try:
            build = value.decode("ascii")
        except UnicodeError:
            return None
        if not re.fullmatch(
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [ 0-9][0-9] "
            r"[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}", build
        ):
            return None
    return build if offset == len(data) else None


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
