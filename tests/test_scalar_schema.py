"""Explicit channel contracts, not a guessed firmware 4.15 record layout."""
import datetime
import struct
from pathlib import Path

import pytest

CONFIG = (Path(__file__).parent / "fixtures/scalar-1xx.txt").read_text()
BASE = datetime.datetime(2026, 1, 1)


def frames(wide=False):
    return (
        struct.pack("<BBBi", 255, 0, 0, 0)
        + struct.pack("<ffHHH" + ("H" if wide else "B"), 12.5, .00002, 47, 150, 64, 258 if wide else 2)
        + struct.pack("<BBBiIfhHH", 0, 0, 3, 100, 300, .0001, 4550, 8750, 32)
        + struct.pack("<BBBiff", 1, 0, 1, 200, 13, .00003)
        + struct.pack("<BBBiIffHH", 2, 0, 2, 300, 100, 14, .00004, 120, 16)
    )


@pytest.mark.parametrize("wide", [False, True])
def test_declared_layout_decodes_following_records_and_preserves_units(protocol, wide):
    config = CONFIG.replace("Id=17\nDType=2", "Id=17\nDType=4") if wide else CONFIG
    status = {}
    decoded = protocol.decode_data_buf(frames(wide), BASE, status, scalar_schemas=protocol.parse_scalar_schemas(config))
    assert status["stop_reason"] == "complete"
    assert len(decoded) == 4
    assert decoded[0].real_time_flags == (258 if wide else 2)
    assert decoded[0].count_rate == 12.5
    assert decoded[0].count_rate_err == pytest.approx(4.7)
    assert decoded[0].dose_rate_err == 15
    assert decoded[1].temperature == 25.5
    assert decoded[1].charge_level == 87.5
    assert decoded[1].dose == pytest.approx(.0001)
    assert decoded[2].count_rate == 13
    assert decoded[3].dose_rate_err == 12
    assert decoded[3].dt == BASE + datetime.timedelta(seconds=3)
    if not wide:
        assert decoded == protocol.decode_data_buf(frames(), BASE)


def test_channel_order_is_declared_not_assumed(protocol):
    a = "[[[CHN_CountRate]]]\nId=3\nDType=1\nRType=1\nUnit=cps\n"
    b = "[[[CHN_DoseRate]]]\nId=4\nDType=1\nRType=1\nUnit=R/h\n"
    schemas = protocol.parse_scalar_schemas(CONFIG.replace(a+b, b+a))
    raw = struct.pack("<BBBi", 1, 0, 0, 0) + struct.pack("<ffHHHB", .00002, 12.5, 10, 20, 0, 0)
    result = protocol.decode_data_buf(raw, BASE, scalar_schemas=schemas)[0]
    assert result.count_rate == 12.5
    assert result.dose_rate == pytest.approx(.00002)


@pytest.mark.parametrize("old,new", [
    ("Id=17\nDType=2", "Id=17\nDType=9"),
    ("Id=17\nDType=2", "Id=17\nDType=2\nDType=4"),
    ("RType=8", "RType=2"), ("RType=1", "RType=8"),
    ("Unit=R/h", "Unit=uSv/h"), ("P1=0.1", "P1=1"),
    ("P1=0.1", "P1=nan"), ("Expr=1", "Expr=2"),
    ("Id=17", "Id=0"), ("CHN_RealTimeFlags", "CHN_Unknown"),
    ("[[GRP_RealTimeData]]", "[[GRP_RealTimeData]"),
    ("DType=1\nRType=1", "DType=1"),
])
def test_unsupported_declared_realtime_group_stops_before_publishing(protocol, old, new):
    status = {}
    records = protocol.decode_data_buf(frames(), BASE, status,
        scalar_schemas=protocol.parse_scalar_schemas(CONFIG.replace(old, new)))
    assert records == []
    assert status["stop_offset"] == 0
    assert status["stop_reason"] == "unsupported_scalar_schema(group=0)"


@pytest.mark.parametrize("config", [CONFIG + CONFIG, "x" * 65537, "[DataStructure]\n" + "x" * 2049])
def test_global_schema_limits_fail_closed(protocol, config):
    assert protocol.parse_scalar_schemas(config) == dict.fromkeys((0, 1, 2, 3, 5))


def test_truncated_declared_records_never_publish_partial_values(protocol):
    raw = frames(True)
    schemas = protocol.parse_scalar_schemas(CONFIG.replace("Id=17\nDType=2", "Id=17\nDType=4"))
    boundaries = [23, 44, 59, 82]
    assert len(raw) == boundaries[-1]
    for length in range(len(raw)):
        result = protocol.decode_data_buf(raw[:length], BASE, scalar_schemas=schemas)
        assert len(result) == sum(length >= boundary for boundary in boundaries)


def test_no_byte_skipping_when_schema_is_missing(protocol):
    status = {}
    assert len(protocol.decode_data_buf(frames(True), BASE, status)) == 1
    assert status["stop_offset"] == 22
    assert status["stop_reason"].startswith("sequence_jump")


def test_layout_diagnostics_include_representation_not_arbitrary_metadata(protocol):
    summary = protocol.describe_record_layout(CONFIG + "\nSensor=private-name\n")
    assert summary[0]["channels"][0]["RType"] == 1
    assert "private-name" not in str(summary)
