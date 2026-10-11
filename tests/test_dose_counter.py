"""Captured modern dose-counter records and configuration-gated decoding.

The six-byte group4 schema appears in the supplied Android APK1.66.03
and Windows application resource. Its independent non-resettable counter
has different semantics from RareData's accumulated dose since reset.
"""

import datetime
import struct

import pytest

BASE_TIME = datetime.datetime(2026, 10, 10, 15, 0)
MODERN_CONFIGURATION = """[DeviceParams]
FrameTimeStep_us=10000
[DataStructure]
[[GRP_DoseCounter]]
Id=4
FlgMsk=0x00009000
[[[CHN_DoseCounter]]]
Id=22
Unit=" uR| мкР"
ScaledUnit=1
DType=8
RType=8
[[[CHN_Flags]]]
Id=0
DType=4
RType=8
"""
LEGACY_CONFIGURATION = """[DataStructure]
[[GRP_UserData]]
Id=4
[[[CHN_Count]]]
Id=2
DType=8
[[[CHN_CountRate]]]
Id=3
DType=1
[[[CHN_DoseRate]]]
Id=4
DType=1
[[[CHN_DoseRateErr]]]
Id=5
DType=4
[[[CHN_Flags]]]
Id=0
DType=4
"""

# Exact13-byte records from the RC-103/FW4.14 log; each was incorrectly
# treated as a truncated16-byte UserData payload before configuration gating.
CAPTURED_RECORDS = [
    ("a60004548b0000199e01000030", 35668, 106009),
    ("170004e96101002d9e01000030", 90601, 106029),
    ("1800041d140000379e01000030", 5149, 106039),
    ("5800046d3e0000419e01000030", 15981, 106049),
    ("3d0004102e00004b9e01000030", 11792, 106059),
]


def _live_record(seq, offset=0):
    return struct.pack("<BBBi", seq, 0, 0, offset) + struct.pack(
        "<ffHHHB", 12.5, 0.00002, 10, 20, 0, 0
    )


def _counter_record(seq=0, offset=0, counter=106009, flags=0x3000):
    return struct.pack("<BBBiIH", seq, 0, 4, offset, counter, flags)


def test_actual_modern_and_legacy_configuration_select_their_own_layout(protocol):
    assert protocol.parse_group4_format(MODERN_CONFIGURATION) is protocol.Group4Format.DOSE_COUNTER
    assert protocol.parse_group4_format(LEGACY_CONFIGURATION) is protocol.Group4Format.LEGACY_USER_DATA


@pytest.mark.parametrize("unit", ["uR", "µR", "μR"])
def test_counter_configuration_recognizes_micro_roentgen_units(protocol, unit):
    config = MODERN_CONFIGURATION.replace('Unit=" uR| мкР"', f'Unit=" {unit}| microR"')
    assert protocol.parse_group4_format(config) is protocol.Group4Format.DOSE_COUNTER


@pytest.mark.parametrize(
    "config",
    [
        "",
        MODERN_CONFIGURATION.replace("[DataStructure]", "[OtherSection]"),
        MODERN_CONFIGURATION.replace("Id=4\n", "Id=5\n", 1),
        MODERN_CONFIGURATION.replace("Id=22\n", "Id=23\n"),
        MODERN_CONFIGURATION.replace("DType=8", "DType=9"),
        MODERN_CONFIGURATION.replace("DType=8", "DType=1"),
        MODERN_CONFIGURATION.replace("DType=4", "DType=5"),
        MODERN_CONFIGURATION.replace("DType=4\n", ""),
        MODERN_CONFIGURATION.replace("Id=4\n", "Id=4\nId=4\n", 1),
        MODERN_CONFIGURATION + MODERN_CONFIGURATION,
        MODERN_CONFIGURATION + LEGACY_CONFIGURATION,
        MODERN_CONFIGURATION.replace("Id=4\n", "Id=invalid\n", 1),
        MODERN_CONFIGURATION.replace("Id=0\n", "Id=22\n"),
        MODERN_CONFIGURATION.replace("GRP_DoseCounter", "GRP_Unknown"),
        MODERN_CONFIGURATION.replace('Unit=" uR| мкР"\n', ""),
        MODERN_CONFIGURATION.replace('Unit=" uR| мкР"', 'Unit=" R| roentgen"'),
        MODERN_CONFIGURATION + "[[[ExtraChannel]]]\nId=23\nDType=4\n",
        LEGACY_CONFIGURATION.replace("DType=8", "DType=2"),
    ],
)
def test_unrecognized_or_ambiguous_configuration_does_not_guess(protocol, config):
    assert protocol.parse_group4_format(config) is None


@pytest.mark.parametrize("wire_hex,offset,counter", CAPTURED_RECORDS)
def test_captured_counter_decode_and_following_live_alignment(protocol, wire_hex, offset, counter):
    wire = bytes.fromhex(wire_hex)
    seq = wire[0]
    status = {}
    records = protocol.decode_data_buf(
        wire + _live_record((seq + 1) % 256, offset + 1),
        BASE_TIME, status,
        group4_format=protocol.parse_group4_format(MODERN_CONFIGURATION),
    )
    assert len(records) == 2
    record, live = records
    assert isinstance(record, protocol.DoseCounterData)
    assert record.dose_uR == counter
    assert record.flags == 0x3000
    assert record.dt == BASE_TIME + datetime.timedelta(milliseconds=offset * 10)
    assert isinstance(live, protocol.RealTimeData)
    assert live.count_rate == pytest.approx(12.5)
    assert status["stop_reason"] == "complete"
    assert status["record_types"] == {"DoseCounterData": 1, "RealTimeData": 1}
    data = protocol.extract_sensor_values(records)
    assert data.dose_counter == counter / 100
    assert data.accumulated_dose is None
    assert data.measurement_time == live.dt
    assert data.measurement_type == "RealTimeData"


def test_legacy_user_data_remains_a_sixteen_byte_skip(protocol):
    legacy = struct.pack("<BBBiIffHH", 1, 0, 4, 20, 60, 10, 0.001, 20, 0)
    status = {}
    records = protocol.decode_data_buf(
        _live_record(0) + legacy + _live_record(2, 21), BASE_TIME, status,
        group4_format=protocol.parse_group4_format(LEGACY_CONFIGURATION),
    )
    assert len(records) == 2
    assert all(isinstance(record, protocol.RealTimeData) for record in records)
    assert records[-1].dt == BASE_TIME + datetime.timedelta(milliseconds=210)
    assert status["stop_reason"] == "complete"
    assert protocol.extract_sensor_values(records).dose_counter is None


@pytest.mark.parametrize("extra_payload", [b"", b"\x00" * 100, _live_record(2)])
def test_unknown_layout_stops_group4_without_using_remaining_length(protocol, extra_payload):
    status = {}
    records = protocol.decode_data_buf(
        _live_record(0) + _counter_record(1) + extra_payload, BASE_TIME, status
    )
    assert len(records) == 1
    assert isinstance(records[0], protocol.RealTimeData)
    assert status["stop_reason"] == "unsupported_group4_schema"
    assert status["stop_offset"] == len(_live_record(0))


@pytest.mark.parametrize("payload_bytes", range(6))
def test_truncated_counter_keeps_only_prior_complete_records(protocol, payload_bytes):
    status = {}
    records = protocol.decode_data_buf(
        _live_record(0) + _counter_record(1)[:7 + payload_bytes], BASE_TIME, status,
        group4_format=protocol.Group4Format.DOSE_COUNTER,
    )
    assert len(records) == 1
    assert status["stop_reason"].startswith("incomplete_record(eid=0,gid=4")
    data = protocol.extract_sensor_values(records)
    assert data.dose_counter is None
    assert data.accumulated_dose is None


@pytest.mark.parametrize("counter", [0, 0xFFFFFFFF])
def test_unsigned_counter_limits_and_conversion(protocol, counter):
    records = protocol.decode_data_buf(
        _counter_record(counter=counter), BASE_TIME,
        group4_format=protocol.Group4Format.DOSE_COUNTER,
    )
    assert records[0].dose_uR == counter
    data = protocol.extract_sensor_values(records)
    assert data.dose_counter == counter / 100
    assert data.dose_rate is None
    assert data.count_rate is None
    assert data.accumulated_dose is None
    assert data.measurement_time is None
    assert data.measurement_type is None
    assert data.measurement_flags is None
    assert data.data_buf_status["measurement_candidates"] == {}


@pytest.mark.parametrize("counter_time_delta", [-128, 128])
def test_independent_counter_never_overrides_accumulated_dose_since_reset(protocol, counter_time_delta):
    rare = protocol.RareData(BASE_TIME, dose=0.00002, charge_level=50, temperature=25)
    counter = protocol.DoseCounterData(
        BASE_TIME + datetime.timedelta(seconds=counter_time_delta), dose_uR=106009
    )
    for records in ([rare, counter], [counter, rare]):
        data = protocol.extract_sensor_values(records)
        assert data.accumulated_dose == pytest.approx(0.2)
        assert data.dose_counter == pytest.approx(1060.09)
        assert data.battery == 50
        assert data.temperature == 25
        assert data.measurement_time is None


def test_accumulated_dose_reset_and_independent_counter_remain_distinct(protocol):
    before_reset = protocol.RareData(BASE_TIME, dose=0.02, charge_level=50, temperature=25)
    after_reset = protocol.RareData(
        BASE_TIME + datetime.timedelta(seconds=1), dose=0, charge_level=50, temperature=25
    )
    counter = protocol.DoseCounterData(BASE_TIME + datetime.timedelta(seconds=128), 106009)
    data = protocol.extract_sensor_values([after_reset, counter, before_reset])
    assert data.accumulated_dose == 0
    assert data.dose_counter == pytest.approx(1060.09)
    assert data.measurement_time is None


def test_latest_counter_by_timestamp_and_last_tie_record(protocol):
    old = protocol.DoseCounterData(BASE_TIME, 100)
    first = protocol.DoseCounterData(BASE_TIME + datetime.timedelta(seconds=1), 110)
    tied = protocol.DoseCounterData(first.dt, 120)
    data = protocol.extract_sensor_values([first, old, tied])
    assert data.dose_counter == 1.2
    assert data.accumulated_dose is None
    assert data.measurement_time is None


@pytest.mark.parametrize("value", [-1, 0x100000000, 1.5, float("nan"), float("inf")])
def test_invalid_counter_does_not_displace_valid_counter(protocol, value):
    good = protocol.DoseCounterData(BASE_TIME, 106009)
    bad = protocol.DoseCounterData(BASE_TIME + datetime.timedelta(seconds=1), value)
    assert protocol.extract_sensor_values([good, bad]).dose_counter == pytest.approx(1060.09)


@pytest.mark.parametrize("channel_dtype", [8, 4])
def test_explicit_zero_expression_is_recognized(protocol, channel_dtype):
    config = MODERN_CONFIGURATION.replace(
        f"DType={channel_dtype}\n", f"DType={channel_dtype}\nExpr=0\n"
    )
    assert protocol.parse_group4_format(config) is protocol.Group4Format.DOSE_COUNTER


@pytest.mark.parametrize("channel_dtype", [8, 4])
@pytest.mark.parametrize(
    "expression", ["1\nP1=2\nP2=10", "2", "-1", "invalid", "0.5", "0\nExpr=1", "0\nExpr=0"]
)
def test_unsupported_malformed_or_duplicate_expression_is_rejected(protocol, channel_dtype, expression):
    config = MODERN_CONFIGURATION.replace(
        f"DType={channel_dtype}\n", f"DType={channel_dtype}\nExpr={expression}\n"
    )
    assert protocol.parse_group4_format(config) is None


@pytest.mark.parametrize("scaled_unit", [0, 1])
def test_display_unit_scaling_does_not_change_raw_counter_value(protocol, scaled_unit):
    # APK cs1.b applies Expr/P1/P2 to values. ScaledUnit is read only by
    # cs1.f when formatting display text; it does not scale the wire counter.
    config = MODERN_CONFIGURATION.replace("ScaledUnit=1", f"ScaledUnit={scaled_unit}")
    records = protocol.decode_data_buf(
        _counter_record(), BASE_TIME,
        group4_format=protocol.parse_group4_format(config),
    )
    assert protocol.extract_sensor_values(records).dose_counter == pytest.approx(1060.09)
