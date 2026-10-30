"""1XX quality and schema regressions; all packet fixtures here are synthetic.

Channel definitions match the inspected Windows 1.16.01 resource 15419 and
APK 1.66.03 configuration. They are not captured firmware 4.15 alarm packets.
"""
import asyncio
import datetime as dt
import math
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_client import client_module, attached_client
from test_coordinator import coordinator_module, clock, make_coordinator, readings
from test_sensor import sensor_module

BASE = dt.datetime(2026, 10, 30, 12)
CHANNELS = (
    ("CountRate", 3, 1, "f", 12.5, ""),
    ("CountRateErr", 16, 4, "H", 510, "Expr=1\nP1=0.1"),
    ("DoseRate", 4, 1, "f", 0.000012, ""),
    ("DoseRateErr", 5, 4, "H", 310, "Expr=1\nP1=0.1"),
    ("Duration", 6, 8, "I", 120, ""),
    ("Dose", 7, 1, "f", 0.001, ""),
    ("Temperature", 8, 5, "h", -100, "Expr=1\nP1=0.01\nP2=-20"),
    ("ChargeLevel", 9, 4, "H", 9500, "Expr=1\nP1=0.01"),
)


def event_config(channels=CHANNELS):
    text = """[DataStructure]
[[GRP_Event]]
Id=7
[[[CHN_Event]]]
Id=14
DType=2
[[[CHN_EventChnMsk]]]
Id=15
DType=2
Expr=1
P1=8
P2=7
[[[CHN_Flags]]]
Id=0
DType=4
"""
    for name, cid, dtype, _, _, expr in channels:
        text += f"[[[CHN_{name}]]]\nId={cid}\nDType={dtype}\n{expr}\n"
    return text


def live(seq=1, offset=10, error=310):
    return struct.pack("<BBBi", seq, 0, 0, offset) + struct.pack("<ffHHHB", 12.5, 0.000012, 510, error, 0x40, 2)


def event(mask, channels=CHANNELS):
    # Code 99 deliberately has no semantic meaning assigned by these tests.
    data = struct.pack("<BBBiBBH", 0, 0, 7, 0, 99, mask, 0x3000)
    return data + b"".join(struct.pack("<" + ch[3], ch[4]) for bit, ch in enumerate(channels) if mask & (1 << bit))


@pytest.mark.parametrize("mask", range(256))
def test_all_declared_event_masks_reach_following_live_record(protocol, mask):
    status = {}
    records = protocol.decode_data_buf(event(mask) + live(), BASE, status, event_schema=protocol.parse_event_schema(event_config()))
    assert status["stop_reason"] == "complete"
    assert len(records) == 2
    assert isinstance(records[0], protocol.DeviceEvent)
    assert records[0].code == 99
    assert set(records[0].values) == {ch[1] for bit, ch in enumerate(CHANNELS) if mask & (1 << bit)}
    if mask & 64:
        assert records[0].values[8] == -21
    data = protocol.extract_sensor_values(records)
    assert data.count_rate == 12.5
    assert data.dose_rate_error_percent == 31
    assert data.count_rate_error_percent == 51
    assert data.real_time_flags == 2
    assert data.data_buf_status["device_events"][0]["channel_mask"] == mask


@pytest.mark.parametrize("cut", range(1, 36))
def test_partial_event_never_publishes_partial_fields(protocol, cut):
    packet = event(255)
    if cut >= len(packet):
        return
    status = {}
    records = protocol.decode_data_buf(packet[:-cut], BASE, status, event_schema=protocol.parse_event_schema(event_config()))
    assert not records
    assert status["stop_reason"] != "complete"


def test_schema_order_is_used_and_undeclared_mask_rejected(protocol):
    channels = (CHANNELS[6], CHANNELS[0])
    schema = protocol.parse_event_schema(event_config(channels))
    assert schema is not None
    records = protocol.decode_data_buf(event(3, channels) + live(), BASE, event_schema=schema)
    assert records[0].values == {8: -21, 3: 12.5}
    status = {}
    assert not protocol.decode_data_buf(event(4) + live(), BASE, status, event_schema=schema)
    assert "unsupported_event_mask" in status["stop_reason"]


@pytest.mark.parametrize("old,new", [
    ("P1=8", "P1=16"), ("P2=7", "P2=0"),
    ("DType=5", "DType=4"), ("Id=8", "Id=9"),
    ("P1=0.01", "P1=nan"), ("P1=0.1", "P1=0.1\nP1=0.2"),
    ("[[GRP_Event]]", "[[GRP_Event]"), ("CHN_CountRate]", "CHN_Unknown]"),
    ("Id=7", "Id=7\nId=7"), ("Expr=1\nP1=8", "P1=8"),
    ("[[[CHN_Flags]]]\nId=0\nDType=4", "[[[CHN_Flags]]]\nId=0\nDType=4\nExpr=1"),
])
def test_unknown_or_conflicting_event_schema_is_rejected(protocol, old, new):
    assert protocol.parse_event_schema(event_config().replace(old, new)) is None


def test_invalid_declared_schema_does_not_fall_back_to_guessed_layout(protocol):
    status = {}
    assert not protocol.decode_data_buf(event(3) + live(), BASE, status, event_schema_required=True)
    assert status["stop_reason"] == "unsupported_event_schema"


def test_event_only_cannot_become_measurement_and_diagnostics_are_bounded(protocol):
    records = [protocol.DeviceEvent(BASE, n, 0, 0, {}) for n in range(10)]
    data = protocol.extract_sensor_values(records)
    assert data.measurement_time is None and data.dose_rate is None
    assert len(data.data_buf_status["device_events"]) == 5


def test_errors_follow_selected_record_and_raw_has_unknown_quality(protocol):
    records = protocol.decode_data_buf(live(0) + live(1, 20, 150), BASE)
    data = protocol.extract_sensor_values(records)
    assert data.dose_rate_error_percent == 15
    records.append(protocol.RawData(BASE + dt.timedelta(days=1), 10, 0.00002))
    assert protocol.extract_sensor_values(records).dose_rate_error_percent == 15
    raw = protocol.extract_sensor_values(records[-1:])
    assert raw.dose_rate_error_percent is None and raw.real_time_flags is None


def test_rare_temperature_is_signed_below_minus_twenty(protocol):
    payload = struct.pack("<BBBiIfhHH", 0, 0, 3, 0, 20, 0.001, -100, 8000, 0)
    data = protocol.extract_sensor_values(protocol.decode_data_buf(payload, BASE))
    assert data.temperature == -21
    assert data.temperature_time == BASE
    assert data.accumulated_dose_time == BASE


@pytest.mark.parametrize("error,limit,temp,expected,reason", [
    (30, 30, 25, "normal", None), (50, 50, 25, "normal", None),
    (30.1, 30, 25, "reduced", "statistical_uncertainty"),
    (50.1, 50, 25, "reduced", "statistical_uncertainty"),
    (15, 30, 45.1, "reduced", "high_temperature"),
    (15, 30, 45, "unknown", "temperature_boundary"),
    (15, 30, -21, "unknown", "low_temperature"),
    (15, 30, None, "unknown", "temperature_unknown"),
    (None, 30, 25, "unknown", "uncertainty_unknown"),
    (math.nan, 30, 25, "unknown", "uncertainty_unknown"),
    (-1, 30, 25, "unknown", "uncertainty_unknown"),
    (31, 30, None, "reduced", "statistical_uncertainty"),
])
def test_calculated_quality_boundaries(protocol, error, limit, temp, expected, reason):
    result = protocol.measurement_quality(error, limit, temp)
    assert result["quality"] == expected
    assert result["quality_source"] == "calculated"
    if reason:
        assert reason in result["quality_reasons"]


def test_coordinator_retains_quality_with_accepted_sample_and_expires_temperature(make_coordinator, protocol, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._schedule_maintenance = lambda: None
        c._client.temperature_observed_monotonic = clock.now
        c._client.get_data.return_value = None
        sample = readings(protocol, dose_rate=0.12, count_rate=12.5, temperature=25,
                          measurement_time=BASE, measurement_type="RealTimeData",
                          dose_rate_error_percent=15, count_rate_error_percent=20, real_time_flags=2)
        c._client.get_data.side_effect = [sample, readings(protocol, dose_rate=0.9, count_rate=30,
            measurement_time=BASE, measurement_type="RealTimeData", dose_rate_error_percent=99)]
        await c.async_refresh()
        assert c.radiation_quality("dose_rate")["quality"] == "normal"
        clock.now += 5
        await c.async_refresh()
        assert c.data.sensors.dose_rate == 0.12
        assert c.radiation_quality("dose_rate")["uncertainty_percent"] == 15
        assert c.data.sensors.real_time_flags == 2
        clock.now += 176
        # Fresh radiation with stale temperature must not assert normal.
        c._client.get_data.side_effect = AsyncMock(return_value=readings(protocol,
            dose_rate=0.12, count_rate=12.5, measurement_time=BASE+dt.timedelta(seconds=181),
            measurement_type="RealTimeData", dose_rate_error_percent=10))
        await c.async_refresh()
        assert c.radiation_quality("dose_rate")["quality"] == "unknown"
        assert c.detector_temperature_status["status"] is None
        await c.async_shutdown()
    asyncio.run(scenario())


def test_temperature_cache_and_failed_register_read_do_not_renew_observation(client_module, monkeypatch):
    async def scenario():
        client, _ = attached_client(client_module)
        client._read_vsfr_batch = AsyncMock(return_value=[25.0])
        assert await client.get_temperature() == 25
        observed = client.temperature_observed_monotonic
        assert await client.get_temperature() == 25
        assert client.temperature_observed_monotonic == observed
        client._last_temperature_read -= 61
        client._read_vsfr_batch.return_value = [None]
        assert await client.get_temperature() == 25
        assert client.temperature_observed_monotonic == observed
        client._last_temperature_read -= 61
        client._read_vsfr_batch.side_effect = TimeoutError
        with pytest.raises(TimeoutError):
            await client.get_temperature()
        assert client.temperature_observed_monotonic == observed
        await client.disconnect()
    asyncio.run(scenario())


def test_firmware_change_invalidates_all_client_protocol_metadata(client_module):
    async def scenario():
        client, _ = attached_client(client_module)
        client._firmware_version = "4.14"
        client._cache_record_configuration(event_config())
        client._spectrum_format_loaded = client._spectrum_format_authoritative = True
        client._temperature_observed_monotonic = 100
        client._firmware_check_pending = True
        client._execute = AsyncMock(return_value=struct.pack("<HHBHHB", 0, 4, 0, 15, 4, 0))
        assert await client.get_firmware_version() == "4.15"
        assert not client._configuration_loaded
        assert client.configuration_retry_due
        assert client._event_schema is None and client._group4_format is None
        assert not client._spectrum_format_loaded
        assert client.temperature_observed_monotonic is None
        assert not client._firmware_check_pending
        await client.disconnect()
    asyncio.run(scenario())


def test_firmware_rechecked_after_reconnect_and_changed_caches_cleared(make_coordinator, protocol, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._serial_number = "RC-103-000123"
        c._fw_version = "4.14"
        c._sfr_file = "old registers"
        c._last_settings = protocol.RadiaCodeSettings(dr_alarm_level1=40)
        c._automatic_spectrum_paused = True
        c._client.get_firmware_version.return_value = "4.15"
        c._connection_count = 1
        await c._fetch_device_identity()
        assert c._identity_connection == 1 and c.firmware_version == "4.15"
        assert c._last_settings.dr_alarm_level1 is None
        assert c._sfr_file == ""
        assert c._client.get_sfr_file.await_count == 1
        assert c._client.get_serial_number.await_count == 0
        assert not c._automatic_spectrum_paused
        await c.async_shutdown()
    asyncio.run(scenario())


def test_temperature_and_dose_alarms_have_independent_expiry(make_coordinator, protocol, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._schedule_maintenance = lambda: None
        c._client.temperature_observed_monotonic = clock.now
        rare = readings(protocol, dose_rate=0.12, count_rate=12,
            measurement_time=BASE, measurement_type="RealTimeData", temperature=46,
            accumulated_dose=10, accumulated_dose_time=BASE)
        c._client.get_data.side_effect = [rare, readings(protocol,
            accumulated_dose=99, accumulated_dose_time=BASE, temperature=20)]
        await c.async_refresh()
        assert c.detector_temperature_status["status"] == "high"
        assert c.accumulated_dose_fresh
        clock.now += 10
        await c.async_refresh()
        assert c._last_temperature == 46
        assert c._last_accumulated_dose == 10
        clock.now += 170
        updates = c.listener_updates
        c._expire_status()
        assert c.listener_updates > updates
        assert c.detector_temperature_status["status"] is None
        assert not c.accumulated_dose_fresh
        await c.async_shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,expected,value", [
    ("count_rate", "L2 Alarm", 60), ("count_rate", "L1 Alarm", 20),
    ("count_rate", "No Alarm", 19.9), ("accumulated_dose", "L2 Alarm", 1.2),
])
def test_calculated_alarm_entities_use_correct_units(sensor_module, protocol, kind, expected, value):
    coordinator = SimpleNamespace(measurement_available=True, accumulated_dose_fresh=True,
        data=SimpleNamespace(sensors=SimpleNamespace(**{kind:value}), settings=protocol.RadiaCodeSettings(
            cr_alarm_level1=200, cr_alarm_level2=600, ds_alarm_level1=40, ds_alarm_level2=120)))
    entry = SimpleNamespace(data={"address":"AA", "name":"RC-103"})
    sensor = sensor_module.RadiaCodeRadiationAlarmSensor(coordinator, entry, kind)
    assert sensor.native_value == expected
    assert sensor.available
    assert sensor.extra_state_attributes["source"] == "calculated"
