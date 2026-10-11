"""Freshness across live, raw, and database streams with distinct time bases."""

import asyncio
from datetime import datetime, timedelta

import pytest

from test_coordinator import (  # noqa: F401
    clock, coordinator_module, make_coordinator, readings, refresh_and_settle,
)


def fallback_record(protocol, kind, timestamp):
    """Use anonymized values from complete mixed-record diagnostic responses."""
    if kind == "RawData":
        return protocol.RawData(timestamp, 16.0, 0.00009238)
    return protocol.DoseRateDB(timestamp, 100, 8.9, 0.0000127, 1.0)


def set_sample(coordinator, protocol, timestamp, kind="RealTimeData", **values):
    coordinator._client.get_data.side_effect = None
    coordinator._client.get_data.return_value = readings(
        protocol, dose_rate=values.pop("dose_rate", 0.1268),
        count_rate=values.pop("count_rate", 8.93),
        measurement_time=timestamp, measurement_type=kind, **values,
    )


@pytest.mark.parametrize("kind,lead", [("RawData", 128), ("DoseRateDB", 43)])
@pytest.mark.parametrize("reverse", [False, True])
def test_future_fallback_does_not_displace_latest_valid_live_pair(
    protocol, kind, lead, reverse,
):
    base = datetime(2026, 1, 1, 12)
    older = protocol.RealTimeData(base, 7.0, 1, 0.00001, 1, 0x2040)
    latest = protocol.RealTimeData(
        base + timedelta(seconds=5), 8.93, 1, 0.00001268, 1, 0x2040,
    )
    future = fallback_record(protocol, kind, base + timedelta(seconds=lead))
    records = [latest, future, older]
    if reverse:
        records.reverse()
    result = protocol.extract_sensor_values(records)
    assert result.measurement_type == "RealTimeData"
    assert result.measurement_time == latest.dt
    assert result.measurement_flags == latest.flags
    assert result.count_rate == latest.count_rate
    assert result.dose_rate == pytest.approx(0.1268)
    assert result.data_buf_status["measurement_candidates"][kind]["latest_time"] == (
        future.dt.isoformat()
    )


def test_invalid_newest_live_pair_does_not_hide_older_valid_live_pair(protocol):
    base = datetime(2026, 1, 1, 12)
    valid = protocol.RealTimeData(base, 0.0, 1, 0.0, 1)
    invalid = protocol.RealTimeData(
        base + timedelta(seconds=5), float("nan"), 1, 0.00001, 1,
    )
    future = fallback_record(protocol, "RawData", base + timedelta(seconds=128))
    result = protocol.extract_sensor_values([valid, future, invalid])
    assert result.measurement_type == "RealTimeData"
    assert result.measurement_time == base
    assert result.count_rate == 0
    assert result.dose_rate == 0


@pytest.mark.parametrize("reverse", [False, True])
def test_without_valid_live_pair_preserves_newest_fallback_and_raw_tie(
    protocol, reverse,
):
    base = datetime(2026, 1, 1, 12)
    invalid = protocol.RealTimeData(base, float("nan"), 1, 0.00001, 1)
    raw = fallback_record(protocol, "RawData", base + timedelta(seconds=5))
    newer_database = fallback_record(
        protocol, "DoseRateDB", base + timedelta(seconds=10),
    )
    records = [invalid, raw, newer_database]
    if reverse:
        records.reverse()
    result = protocol.extract_sensor_values(records)
    assert result.measurement_type == "DoseRateDB"
    assert result.measurement_time == newer_database.dt
    tied_raw = fallback_record(protocol, "RawData", newer_database.dt)
    records = [newer_database, tied_raw]
    if reverse:
        records.reverse()
    tied = protocol.extract_sensor_values(records)
    assert tied.measurement_type == "RawData"
    assert tied.measurement_time == tied_raw.dt
    assert tied.count_rate == tied_raw.count_rate
    assert tied.dose_rate == pytest.approx(tied_raw.dose_rate * 10_000)


@pytest.mark.parametrize("kind,lead", [
    ("RawData", 128), ("DoseRateDB", 43), (None, 128),
])
def test_fallback_only_poll_cannot_poison_later_live_timestamp(
    make_coordinator, clock, protocol, kind, lead,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        base = datetime(2026, 1, 1, 12)
        set_sample(coordinator, protocol, base + timedelta(seconds=lead), kind)
        await refresh_and_settle(coordinator)
        assert coordinator._last_fresh_monotonic == 1000
        clock.now += 5
        set_sample(coordinator, protocol, base + timedelta(seconds=5), dose_rate=0.13)
        snapshot = await refresh_and_settle(coordinator)
        assert coordinator.last_update_success
        assert coordinator.measurement_available
        assert snapshot.sensors.dose_rate == 0.13
        assert snapshot.sensors.measurement_type == "RealTimeData"
        assert snapshot.sensors.measurement_time == base + timedelta(seconds=5)
        assert coordinator._last_fresh_monotonic == 1005
        assert coordinator._last_sample_progress == {
            "device_seconds": None, "receipt_seconds": 5,
        }
        clock.now += 5
        set_sample(coordinator, protocol, base + timedelta(seconds=10))
        await refresh_and_settle(coordinator)
        assert coordinator._last_sample_progress == {
            "device_seconds": 5, "receipt_seconds": 5,
        }
        await coordinator.async_shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["RawData", "DoseRateDB", None])
def test_alternating_replayed_streams_do_not_renew_the_measurement_lease(
    make_coordinator, clock, protocol, kind,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        base = datetime(2026, 1, 1, 12)
        set_sample(coordinator, protocol, base)
        await refresh_and_settle(coordinator)
        clock.now += 5
        future = base + timedelta(seconds=128)
        set_sample(coordinator, protocol, future, kind)
        await refresh_and_settle(coordinator)
        accepted_receipt = coordinator._last_fresh_monotonic
        assert accepted_receipt == 1005
        assert coordinator.data.sensors.measurement_type == kind
        for timestamp, source, reason in (
            (base, "RealTimeData", "repeated_timestamp"),
            (future, kind, "repeated_timestamp"),
            (base - timedelta(seconds=1), "RealTimeData", "regressed_timestamp"),
            (future - timedelta(seconds=1), kind, "regressed_timestamp"),
        ):
            clock.now += 5
            set_sample(coordinator, protocol, timestamp, source)
            await refresh_and_settle(coordinator)
            assert coordinator._last_sample_rejection == reason
            assert coordinator._last_fresh_monotonic == accepted_receipt
            assert coordinator.measurement_available
        clock.now = accepted_receipt + 60
        set_sample(coordinator, protocol, base)
        await refresh_and_settle(coordinator)
        assert coordinator._last_sample_rejection == "repeated_timestamp"
        assert coordinator._last_fresh_monotonic == accepted_receipt
        assert not coordinator.last_update_success
        assert not coordinator.measurement_available
        assert coordinator.controls_available
        assert coordinator.is_ble_connected
        await coordinator.async_shutdown()
    asyncio.run(scenario())


def test_connection_change_resets_all_streams_without_renewing_cached_lease(
    make_coordinator, clock, protocol,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        base = datetime(2026, 1, 1, 12)
        set_sample(coordinator, protocol, base)
        await refresh_and_settle(coordinator)
        clock.now += 5
        set_sample(coordinator, protocol, base + timedelta(seconds=128), "RawData")
        await refresh_and_settle(coordinator)
        accepted_receipt = coordinator._last_fresh_monotonic
        old_connection = coordinator.connection_count
        coordinator._client.is_connected = False
        coordinator._client.get_data.return_value = readings(protocol)
        clock.now += 5
        await refresh_and_settle(coordinator)
        assert coordinator.connection_count == old_connection + 1
        assert coordinator.measurement_available
        assert coordinator._last_fresh_monotonic == accepted_receipt
        assert coordinator._last_sample_rejection == "no_paired_measurement"
        clock.now += 5
        live = base - timedelta(seconds=10)
        set_sample(coordinator, protocol, live)
        await refresh_and_settle(coordinator)
        assert coordinator._last_fresh_monotonic == clock.now
        assert coordinator._last_sample_progress == {
            "device_seconds": None, "receipt_seconds": None,
        }
        clock.now += 5
        set_sample(coordinator, protocol, base - timedelta(seconds=9), "RawData")
        await refresh_and_settle(coordinator)
        assert coordinator._last_sample_rejection is None
        assert coordinator._last_fresh_monotonic == clock.now
        clock.now += 5
        receipt = coordinator._last_fresh_monotonic
        set_sample(coordinator, protocol, live)
        await refresh_and_settle(coordinator)
        assert coordinator._last_sample_rejection == "repeated_timestamp"
        assert coordinator._last_fresh_monotonic == receipt
        await coordinator.async_shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("rejection", ["no_paired_measurement", "suppressed_outlier"])
def test_rejected_new_stream_does_not_advance_its_watermark(
    make_coordinator, clock, protocol, rejection,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        base = datetime(2026, 1, 1, 12)
        set_sample(coordinator, protocol, base)
        await refresh_and_settle(coordinator)
        future = base + timedelta(seconds=128)
        clock.now += 5
        values = {"dose_rate": None} if rejection == "no_paired_measurement" else {
            "dose_rate": 40000, "count_rate": 1000000,
        }
        set_sample(coordinator, protocol, future, "RawData", **values)
        await refresh_and_settle(coordinator)
        assert coordinator._last_sample_rejection == rejection
        assert coordinator._last_fresh_monotonic == 1000
        clock.now += 5
        set_sample(coordinator, protocol, future, "RawData")
        await refresh_and_settle(coordinator)
        assert coordinator._last_sample_rejection is None
        assert coordinator._last_fresh_monotonic == clock.now
        assert coordinator.data.sensors.measurement_time == future
        await coordinator.async_shutdown()
    asyncio.run(scenario())


def test_advancing_live_timestamp_after_empty_and_replay_at_same_receipt_time(
    make_coordinator, clock, protocol,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        base = datetime(2026, 1, 1, 12)
        set_sample(coordinator, protocol, base)
        await refresh_and_settle(coordinator)
        coordinator._client.get_data.return_value = readings(protocol)
        await refresh_and_settle(coordinator)
        assert coordinator._last_sample_rejection == "no_paired_measurement"
        set_sample(coordinator, protocol, base)
        await refresh_and_settle(coordinator)
        assert coordinator._last_sample_rejection == "repeated_timestamp"
        next_time = base + timedelta(seconds=1)
        set_sample(coordinator, protocol, next_time)
        await refresh_and_settle(coordinator)
        assert coordinator._last_sample_rejection is None
        assert coordinator.data.sensors.measurement_time == next_time
        assert coordinator._last_sample_progress == {
            "device_seconds": 1, "receipt_seconds": 0,
        }
        assert coordinator.measurement_available
        await coordinator.async_shutdown()
    asyncio.run(scenario())
