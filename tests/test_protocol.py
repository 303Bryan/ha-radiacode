"""Unit tests for the RadiaCode BLE protocol layer (pure data, no I/O)."""

import datetime
import struct

import pytest

BASE_TIME = datetime.datetime(2026, 1, 1, 12, 0, 0)


# ── Command builder ───────────────────────────────────────────────────────────


def test_build_command_no_args(protocol):
    packet = protocol.build_command(0x0A04, 3)
    # [len=4] [cmd_lo cmd_hi] [0x00] [0x80+seq]
    assert packet == bytes.fromhex("04000000") + bytes([0x04, 0x0A, 0x00, 0x83])


def test_build_command_with_args(protocol):
    packet = protocol.build_command(0x0007, 0, b"\x01\xff\x12\xff")
    assert packet == bytes.fromhex("08000000") + bytes(
        [0x07, 0x00, 0x00, 0x80, 0x01, 0xFF, 0x12, 0xFF]
    )


def test_build_command_seq_wraps_at_32(protocol):
    packet = protocol.build_command(0x0007, 33)
    assert packet[7] == 0x80 + 1


# ── Response echo header ──────────────────────────────────────────────────────


def test_parse_response_body_valid(protocol):
    body = bytes([0x07, 0x00, 0x00, 0x85]) + b"payload"
    assert protocol.parse_response_body(body, 0x0007, 5) == b"payload"


def test_parse_response_body_cmd_mismatch(protocol):
    body = bytes([0x08, 0x00, 0x00, 0x85]) + b"payload"
    with pytest.raises(ValueError, match="echo header mismatch"):
        protocol.parse_response_body(body, 0x0007, 5)


def test_parse_response_body_seq_mismatch(protocol):
    body = bytes([0x07, 0x00, 0x00, 0x86]) + b"payload"
    with pytest.raises(ValueError, match="echo header mismatch"):
        protocol.parse_response_body(body, 0x0007, 5)


def test_parse_response_body_too_short(protocol):
    with pytest.raises(ValueError, match="too short"):
        protocol.parse_response_body(b"\x07\x00", 0x0007, 0)


# ── VS (virtual string) responses ─────────────────────────────────────────────


def test_parse_vs_response_valid(protocol):
    payload = struct.pack("<II", 1, 5) + b"hello"
    assert protocol.parse_vs_response(payload) == b"hello"


def test_parse_vs_response_strips_trailing_null(protocol):
    # Firmware bug: stray trailing null byte after declared length
    payload = struct.pack("<II", 1, 5) + b"hello\x00"
    assert protocol.parse_vs_response(payload) == b"hello"


def test_parse_vs_response_bad_retcode(protocol):
    payload = struct.pack("<II", 0, 5) + b"hello"
    with pytest.raises(ValueError, match="retcode"):
        protocol.parse_vs_response(payload)


def test_parse_vs_response_truncated_returns_partial(protocol):
    # BT proxy buffer exhaustion: fewer bytes than declared — keep partial data
    payload = struct.pack("<II", 1, 100) + b"part"
    assert protocol.parse_vs_response(payload) == b"part"


def test_parse_vs_response_overlong_raises(protocol):
    payload = struct.pack("<II", 1, 2) + b"toolong"
    with pytest.raises(ValueError, match="length mismatch"):
        protocol.parse_vs_response(payload)


# ── VSFR responses ────────────────────────────────────────────────────────────


def test_parse_vsfr_batch_single_float(protocol):
    payload = struct.pack("<I", 0b1) + struct.pack("<f", 25.5)
    values = protocol.parse_vsfr_batch_response(payload, [protocol.VSFR.TEMP_degC])
    assert values == [25.5]


def test_parse_vsfr_batch_invalid_register_is_none(protocol):
    payload = struct.pack("<I", 0)
    values = protocol.parse_vsfr_batch_response(payload, [protocol.VSFR.TEMP_degC])
    assert values == [None]


def test_parse_vsfr_batch_mixed_validity(protocol):
    ids = [protocol.VSFR.SOUND_ON, protocol.VSFR.VIBRO_ON, protocol.VSFR.DISP_BRT]
    # Registers 0 and 2 valid; register 1 omitted from payload entirely
    payload = struct.pack("<III", 0b101, 1, 7)
    values = protocol.parse_vsfr_batch_response(payload, ids)
    assert values == [1, None, 7]


def test_parse_vsfr_batch_too_short_raises(protocol):
    payload = struct.pack("<I", 0b11) + struct.pack("<I", 1)  # claims 2, has 1
    with pytest.raises(ValueError, match="too short"):
        protocol.parse_vsfr_batch_response(
            payload, [protocol.VSFR.SOUND_ON, protocol.VSFR.VIBRO_ON]
        )


def test_parse_write_response(protocol):
    assert protocol.parse_write_response(struct.pack("<I", 1)) is True
    assert protocol.parse_write_response(struct.pack("<I", 0)) is False
    with pytest.raises(ValueError):
        protocol.parse_write_response(b"\x01")


def test_parse_vsfr_read_response(protocol):
    payload = struct.pack("<I", 1) + struct.pack("<f", -3.25)
    assert protocol.parse_vsfr_read_response(payload, protocol.VSFR.TEMP_degC) == -3.25

    payload = struct.pack("<II", 1, 42)
    assert protocol.parse_vsfr_read_response(payload, protocol.VSFR.CPS) == 42

    with pytest.raises(ValueError, match="retcode"):
        protocol.parse_vsfr_read_response(struct.pack("<II", 0, 0), protocol.VSFR.CPS)


# ── data_buf decoding ─────────────────────────────────────────────────────────


def _real_time_record(seq, ts_offset=0, count_rate=10.5, dose_rate=1.23e-4):
    header = struct.pack("<BBBi", seq, 0, 0, ts_offset)
    body = struct.pack("<ffHHHB", count_rate, dose_rate, 15, 20, 0, 0)
    return header + body


def _rare_data_record(seq, ts_offset=0, dose=0.001, temp_c=25.5, charge_pct=87.0):
    header = struct.pack("<BBBi", seq, 0, 3, ts_offset)
    body = struct.pack(
        "<IfHHH", 600, dose, int(temp_c * 100 + 2000), int(charge_pct * 100), 0
    )
    return header + body


def test_decode_real_time_data(protocol):
    records = protocol.decode_data_buf(_real_time_record(0), BASE_TIME)
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, protocol.RealTimeData)
    assert rec.count_rate == pytest.approx(10.5)
    assert rec.dose_rate == pytest.approx(1.23e-4)
    assert rec.count_rate_err == pytest.approx(1.5)
    assert rec.dose_rate_err == pytest.approx(2.0)
    assert rec.dt == BASE_TIME


def test_decode_rare_data(protocol):
    records = protocol.decode_data_buf(_rare_data_record(0, ts_offset=100), BASE_TIME)
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, protocol.RareData)
    assert rec.dose == pytest.approx(0.001)
    assert rec.temperature == pytest.approx(25.5)
    assert rec.charge_level == pytest.approx(87.0)
    # ts_offset is in 10 ms units
    assert rec.dt == BASE_TIME + datetime.timedelta(seconds=1)


def test_decode_multiple_records_sequential(protocol):
    data = _real_time_record(7) + _rare_data_record(8)
    records = protocol.decode_data_buf(data, BASE_TIME)
    assert len(records) == 2


def test_decode_stops_on_sequence_jump(protocol):
    data = _real_time_record(0) + _rare_data_record(5)  # expected seq 1, got 5
    records = protocol.decode_data_buf(data, BASE_TIME)
    assert len(records) == 1


def test_decode_stops_on_unknown_record_type(protocol):
    unknown = struct.pack("<BBBi", 0, 0, 99, 0) + b"\x00" * 16
    records = protocol.decode_data_buf(unknown, BASE_TIME)
    assert records == []


def test_decode_truncated_record_stops_cleanly(protocol):
    data = _real_time_record(0) + _real_time_record(1)[:10]
    records = protocol.decode_data_buf(data, BASE_TIME)
    assert len(records) == 1


def test_decode_empty_buffer(protocol):
    assert protocol.decode_data_buf(b"", BASE_TIME) == []


# ── Sensor value extraction ───────────────────────────────────────────────────


def test_extract_sensor_values_converts_units(protocol):
    records = protocol.decode_data_buf(
        _real_time_record(0) + _rare_data_record(1), BASE_TIME
    )
    data = protocol.extract_sensor_values(records)
    # R/h → µSv/h and R → µSv are both ×10 000
    assert data.dose_rate == pytest.approx(1.23, rel=1e-4)
    assert data.count_rate == pytest.approx(10.5)
    assert data.accumulated_dose == pytest.approx(10.0)
    assert data.battery == pytest.approx(87.0)
    assert data.temperature == pytest.approx(25.5)


def test_extract_sensor_values_empty(protocol):
    data = protocol.extract_sensor_values([])
    assert data.dose_rate is None
    assert data.count_rate is None
    assert data.accumulated_dose is None
    assert data.battery is None
    assert data.temperature is None


def test_extract_sensor_values_uses_most_recent(protocol):
    records = protocol.decode_data_buf(
        _real_time_record(0, dose_rate=1e-4) + _real_time_record(1, dose_rate=2e-4),
        BASE_TIME,
    )
    data = protocol.extract_sensor_values(records)
    assert data.dose_rate == pytest.approx(2.0, rel=1e-4)


# ── Settings decoding ─────────────────────────────────────────────────────────


def test_decode_settings_full(protocol):
    values = [1, 0, 1, 0, 7, 2, 1, 100, 1000, 50000, 100000, 300, 1200]
    settings = protocol.decode_settings(values)
    assert settings.sound_on is True
    assert settings.vibro_on is False
    assert settings.display_on is True
    assert settings.display_backlight_on is False
    assert settings.display_brightness == 7
    assert settings.display_off_time == 2
    assert settings.display_direction == 1
    assert settings.dr_alarm_level1 == 100
    assert settings.dr_alarm_level2 == 1000
    assert settings.ds_alarm_level1 == 50000
    assert settings.ds_alarm_level2 == 100000
    assert settings.cr_alarm_level1 == 300
    assert settings.cr_alarm_level2 == 1200


def test_decode_settings_with_invalid_registers(protocol):
    settings = protocol.decode_settings([None] * 13)
    assert settings.sound_on is None
    assert settings.dr_alarm_level1 is None


# ── Identity decoders ─────────────────────────────────────────────────────────


def test_decode_serial_number(protocol):
    assert protocol.decode_serial_number(b"RC-103-012345\x00") == "RC-103-012345"


def test_parse_firmware_version(protocol):
    data = (
        struct.pack("<HH", 1, 4)        # boot 4.1
        + bytes([3]) + b"abc"           # boot date
        + struct.pack("<HH", 8, 4)      # target 4.8
        + bytes([3]) + b"def"           # target date
    )
    assert protocol.parse_firmware_version(data) == "4.8"


def test_parse_firmware_version_truncated(protocol):
    assert protocol.parse_firmware_version(b"") == "unknown"
    assert protocol.parse_firmware_version(struct.pack("<HH", 1, 4)) == "unknown"


# ── Record validation (corrupt-data rejection) ────────────────────────────────


def test_extract_skips_nonfinite_dose_rate(protocol):
    good = _real_time_record(0, dose_rate=1e-4)
    bad = struct.pack("<BBBi", 1, 0, 0, 0) + struct.pack(
        "<ffHHHB", 10.5, float("nan"), 15, 20, 0, 0
    )
    data = protocol.extract_sensor_values(protocol.decode_data_buf(good + bad, BASE_TIME))
    # The corrupt record must not overwrite the good reading
    assert data.dose_rate == pytest.approx(1.0, rel=1e-4)


def test_extract_skips_negative_dose_rate(protocol):
    bad = struct.pack("<BBBi", 0, 0, 0, 0) + struct.pack(
        "<ffHHHB", 10.5, -5.0, 15, 20, 0, 0
    )
    data = protocol.extract_sensor_values(protocol.decode_data_buf(bad, BASE_TIME))
    assert data.dose_rate is None


def test_extract_skips_implausible_rare_data(protocol):
    # charge_level far above 100% → record rejected entirely
    header = struct.pack("<BBBi", 0, 0, 3, 0)
    body = struct.pack("<IfHHH", 600, 0.001, 4550, 65000, 0)  # charge=650%
    data = protocol.extract_sensor_values(
        protocol.decode_data_buf(header + body, BASE_TIME)
    )
    assert data.battery is None
    assert data.accumulated_dose is None


# ── SpikeFilter ───────────────────────────────────────────────────────────────


def test_spike_filter_passes_normal_values(protocol):
    f = protocol.SpikeFilter(factor=50.0, pass_below=5.0)
    assert f.filter(0.15) == 0.15
    assert f.filter(0.18) == 0.18
    assert f.filter(0.12) == 0.12
    assert f.last_suppressed is None


def test_spike_filter_passes_small_absolute_values(protocol):
    f = protocol.SpikeFilter(factor=50.0, pass_below=5.0)
    assert f.filter(0.01) == 0.01
    # 400× jump but still ≤ pass_below → passes
    assert f.filter(4.0) == 4.0


def test_spike_filter_suppresses_single_outlier(protocol):
    f = protocol.SpikeFilter(factor=50.0, pass_below=5.0)
    assert f.filter(0.15) == 0.15
    assert f.filter(40000.0) is None          # corrupt spike held back
    assert f.last_suppressed == 40000.0
    assert f.filter(0.16) == 0.16             # back to normal → spike discarded
    assert f.last_suppressed is None


def test_spike_filter_confirms_sustained_event(protocol):
    f = protocol.SpikeFilter(factor=50.0, pass_below=5.0)
    assert f.filter(0.15) == 0.15
    assert f.filter(500.0) is None            # first high sample held
    assert f.filter(520.0) == 520.0           # consistent second sample → real
    assert f.filter(510.0) == 510.0           # subsequent samples flow through


def test_spike_filter_rejects_inconsistent_spikes(protocol):
    f = protocol.SpikeFilter(factor=50.0, pass_below=5.0)
    assert f.filter(0.15) == 0.15
    assert f.filter(40000.0) is None          # garbage
    assert f.filter(3e9) is None              # different garbage — not confirmation
    assert f.filter(0.15) == 0.15


def test_spike_filter_rejects_nonfinite(protocol):
    f = protocol.SpikeFilter()
    assert f.filter(float("nan")) is None
    assert f.filter(float("inf")) is None
    assert f.filter(-1.0) is None
    assert f.filter(0.2) == 0.2


def test_spike_filter_none_passthrough(protocol):
    f = protocol.SpikeFilter()
    assert f.filter(None) is None
    assert f.last_suppressed is None


def test_spike_filter_first_reading_accepted(protocol):
    # No baseline yet — first reading is always accepted (whatever it is)
    f = protocol.SpikeFilter(factor=50.0, pass_below=5.0)
    assert f.filter(120.0) == 120.0


# ── Hardness ──────────────────────────────────────────────────────────────────


def test_compute_hardness_matches_app_formula(protocol):
    # 0.15 µSv/h = 15 µR/h at 10 cps → hardness 1.5
    assert protocol.compute_hardness(0.15, 10.0) == pytest.approx(1.5)


def test_compute_hardness_undefined_without_counts(protocol):
    assert protocol.compute_hardness(0.15, 0.0) is None
    assert protocol.compute_hardness(0.15, None) is None
    assert protocol.compute_hardness(None, 10.0) is None


def test_compute_hardness_zero_dose_rate(protocol):
    assert protocol.compute_hardness(0.0, 10.0) == 0.0


# ── Device-health diagnostics ─────────────────────────────────────────────────


def test_diagnostic_batch_decodes_hardware_values(protocol):
    # Values observed on RC-103 FW 4.14: VBIAS 26963 mV, MCU temp raw 2971
    # (centi-°C → 29.71 °C), Vref 2093 mV.
    payload = struct.pack("<IIII", 0b111, 26963, 2971, 2093)
    values = protocol.parse_vsfr_batch_response(payload, protocol.DIAGNOSTIC_VSFR_IDS)
    diag = protocol.decode_diagnostics(values)
    assert diag.sipm_bias_mv == 26963
    assert diag.mcu_temperature == pytest.approx(29.71)
    assert diag.mcu_vref_mv == 2093


def test_diagnostic_batch_rejected_registers_are_none(protocol):
    # Device rejects everything except VBIAS (bit 0)
    payload = struct.pack("<II", 0b001, 26963)
    values = protocol.parse_vsfr_batch_response(payload, protocol.DIAGNOSTIC_VSFR_IDS)
    diag = protocol.decode_diagnostics(values)
    assert diag.sipm_bias_mv == 26963
    assert diag.mcu_temperature is None
    assert diag.mcu_vref_mv is None


def test_signed_int16_register_format(protocol):
    # ACC registers (excluded from the health batch — firmware rejects them
    # over BLE) keep their signed-int16 format in the table: -512 must
    # decode as -512, not 65024.
    raw = struct.unpack("<I", struct.pack("<hxx", -512))[0]
    payload = struct.pack("<II", 0b1, raw)
    values = protocol.parse_vsfr_batch_response(payload, [protocol.VSFR.ACC_X])
    assert values == [-512]


# ── SFR directory decoder ─────────────────────────────────────────────────────


def test_decode_sfr_file_plain_ascii(protocol):
    data = b"0x0500 1 int unsigned DEVICE_CTRL\n0x8024 4 float TEMP_degC\x00\x00"
    text = protocol.decode_sfr_file(data)
    assert text.splitlines() == [
        "0x0500 1 int unsigned DEVICE_CTRL",
        "0x8024 4 float TEMP_degC",
    ]


def test_decode_sfr_file_tolerates_truncated_bytes(protocol):
    # BT-proxy truncation can cut mid-character; non-ASCII bytes must not raise
    data = b"0x0500 1 int unsigned DEVICE\xff"
    text = protocol.decode_sfr_file(data)
    assert text.startswith("0x0500 1 int unsigned DEVICE")


def test_decode_sfr_file_empty(protocol):
    assert protocol.decode_sfr_file(b"") == ""
    assert protocol.decode_sfr_file(b"\x00\x00") == ""


# ── Gamma spectrum ────────────────────────────────────────────────────────────


def _spectrum_header(duration=600, a0=-5.0, a1=2.5, a2=0.001):
    return struct.pack("<Ifff", duration, a0, a1, a2)


def test_decode_spectrum_v0(protocol):
    data = _spectrum_header() + struct.pack("<IIII", 10, 20, 0, 5)
    s = protocol.decode_spectrum(data, 0)
    assert s.duration_s == 600
    assert s.a0 == pytest.approx(-5.0)
    assert s.a1 == pytest.approx(2.5)
    assert s.a2 == pytest.approx(0.001)
    assert s.counts == [10, 20, 0, 5]
    assert s.truncated is False


def test_decode_spectrum_v1_all_encodings(protocol):
    # Group 1: 2 zero-valued channels (vlen=0, no payload)
    g_zeros = struct.pack("<H", (2 << 4) | 0)
    # Group 2: 1 absolute uint8 value (vlen=1): 200
    g_u8 = struct.pack("<H", (1 << 4) | 1) + struct.pack("<B", 200)
    # Group 3: 2 int8 deltas from last=200 (vlen=2): +5 → 205, -10 → 195
    g_d8 = struct.pack("<H", (2 << 4) | 2) + struct.pack("<bb", 5, -10)
    # Group 4: 1 int16 delta from 195 (vlen=3): +1000 → 1195
    g_d16 = struct.pack("<H", (1 << 4) | 3) + struct.pack("<h", 1000)
    # Group 5: 1 int24 delta from 1195 (vlen=4): +65536 → 66731
    g_d24 = struct.pack("<H", (1 << 4) | 4) + struct.pack("<BBb", 0, 0, 1)
    # Group 6: 1 int32 delta from 66731 (vlen=5): -66731 → 0
    g_d32 = struct.pack("<H", (1 << 4) | 5) + struct.pack("<i", -66731)

    data = _spectrum_header() + g_zeros + g_u8 + g_d8 + g_d16 + g_d24 + g_d32
    s = protocol.decode_spectrum(data, 1)
    assert s.counts == [0, 0, 200, 205, 195, 1195, 66731, 0]
    assert s.truncated is False


def test_decode_spectrum_v1_truncated_keeps_leading_channels(protocol):
    g_u8 = struct.pack("<H", (1 << 4) | 1) + struct.pack("<B", 42)
    # Second group claims 3 int16 deltas but only 1 is present
    g_cut = struct.pack("<H", (3 << 4) | 3) + struct.pack("<h", 7)
    s = protocol.decode_spectrum(_spectrum_header() + g_u8 + g_cut, 1)
    assert s.counts == [42, 49]
    assert s.truncated is True


def test_decode_spectrum_header_too_short_raises(protocol):
    with pytest.raises(ValueError):
        protocol.decode_spectrum(b"\x00\x01", 1)


def test_channel_to_kev(protocol):
    # E(ch) = a0 + a1*ch + a2*ch^2
    assert protocol.channel_to_kev(0, -5.0, 2.5, 0.001) == pytest.approx(-5.0)
    assert protocol.channel_to_kev(100, -5.0, 2.5, 0.001) == pytest.approx(255.0)


def test_parse_spec_format_version(protocol):
    assert protocol.parse_spec_format_version("Foo=1\nSpecFormatVersion=1\nBar=2") == 1
    assert protocol.parse_spec_format_version("SpecFormatVersion=0") == 0
    # Absent or unparsable → default 1 (all supported firmware)
    assert protocol.parse_spec_format_version("NoSuchKey=5") == 1
    assert protocol.parse_spec_format_version("SpecFormatVersion=abc") == 1
