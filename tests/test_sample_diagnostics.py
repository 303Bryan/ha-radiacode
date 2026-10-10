"""Explain successful transport reads that fail measurement freshness gates."""

import asyncio
import struct
from datetime import datetime, timedelta

import pytest

from test_coordinator import (  # noqa: F401
    clock, coordinator_module, make_coordinator, readings, refresh_and_settle,
)


def test_decoder_reports_stopping_boundary_without_raw_payload(protocol):
    payload = struct.pack("<BBBi", 0, 0, 99, 0) + bytes(16)
    status = {}
    assert protocol.decode_data_buf(payload, datetime(2026, 1, 1), status) == []
    assert status == {
        "payload_bytes": 23, "decoded_records": 0, "record_types": {},
        "record_group_counts": {"0:99": 1},
        "stop_reason": "unknown_record(eid=0,gid=99)",
        "stop_offset": 0, "unread_bytes": 23,
    }


def test_known_skipped_records_are_counted_without_inventing_measurements(protocol):
    # Five 23-byte UserData records form a complete 115-byte buffer but do not
    # provide a typed radiation measurement. This is a synthetic example.
    payload = b"".join(
        struct.pack("<BBBiIffHH", seq, 0, 4, seq * 100, 10, 9, 0.00001, 10, 0)
        for seq in range(5)
    )
    status = {}
    assert len(payload) == 115
    assert protocol.decode_data_buf(payload, datetime(2026, 1, 1), status) == []
    assert status["record_group_counts"] == {"0:4": 5}
    assert status["record_types"] == {}
    assert status["stop_reason"] == "complete"
    assert status["unread_bytes"] == 0


def test_truncated_sample_block_is_reported_as_incomplete(protocol):
    payload = struct.pack("<BBBiHI", 0, 1, 1, 0, 3, 1000) + bytes(8)
    status = {}
    assert protocol.decode_data_buf(payload, datetime(2026, 1, 1), status) == []
    assert status["stop_reason"].startswith("incomplete_record(eid=1,gid=1")
    assert status["unread_bytes"] == len(payload)


def test_candidate_diagnostics_distinguish_live_history_and_invalid_pairs(protocol):
    now = datetime(2026, 1, 1)
    live = protocol.RealTimeData(now, 10, 1, 0.00001, 1)
    history = protocol.DoseRateDB(now + timedelta(seconds=20), 100, 9, 0.00002, 1)
    invalid = protocol.RawData(now, float("nan"), 0.00001)
    result = protocol.extract_sensor_values([live, history, invalid])
    assert result.measurement_type == "DoseRateDB"
    candidates = result.data_buf_status["measurement_candidates"]
    assert candidates["RealTimeData"]["valid_pairs"] == 1
    assert candidates["DoseRateDB"]["latest_time"] == history.dt.isoformat()
    assert candidates["RawData"]["invalid_pairs"] == 1
    assert candidates["RawData"]["latest_time"] is None


@pytest.mark.parametrize("reason", [
    "no_paired_measurement", "no_measurement_timestamp",
    "repeated_timestamp", "regressed_timestamp", "suppressed_outlier",
])
def test_rejected_samples_report_reason_without_renewing_lease(
    make_coordinator, clock, protocol, reason,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        first = await refresh_and_settle(coordinator)
        timestamp = first.sensors.measurement_time
        values = {"dose_rate": 0.12, "count_rate": 12,
                  "measurement_time": timestamp + timedelta(seconds=5)}
        if reason == "no_paired_measurement":
            values["dose_rate"] = None
        elif reason == "no_measurement_timestamp":
            values["measurement_time"] = None
        elif reason == "repeated_timestamp":
            values["measurement_time"] = timestamp
        elif reason == "regressed_timestamp":
            values["measurement_time"] = timestamp - timedelta(seconds=1)
        else:
            values.update(dose_rate=40000, count_rate=1000000)
        coordinator._client.get_data.side_effect = None
        coordinator._client.get_data.return_value = readings(
            protocol, **values, data_buf_status={"stop_reason": "complete"},
        )
        clock.now += 41
        await refresh_and_settle(coordinator)
        assert coordinator.measurement_available
        assert coordinator.is_ble_connected
        assert coordinator._last_fresh_monotonic == 1000
        status = coordinator.runtime_status
        assert status["freshness"]["last_sample_rejection"] == reason
        assert status["freshness"]["consecutive_polls_without_sample"] == 1
        assert status["data_buf"]["recent_samples"][-1]["accepted"] is False
        clock.now += 20
        assert coordinator.runtime_status["data_buf"]["last_poll_age_seconds"] == 20
        assert not coordinator.measurement_available
        assert coordinator.controls_available
        await coordinator.async_shutdown()
    asyncio.run(scenario())


def test_sample_history_is_bounded_and_new_sample_clears_rejection(
    make_coordinator, clock, protocol,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        await refresh_and_settle(coordinator)
        original_operation = coordinator._client.get_data.side_effect
        coordinator._client.get_data.side_effect = None
        coordinator._client.get_data.return_value = readings(protocol)
        for _ in range(25):
            clock.now += 1
            await refresh_and_settle(coordinator)
        assert len(coordinator.runtime_status["data_buf"]["recent_samples"]) == 20
        assert coordinator._consecutive_no_sample == 25
        coordinator._client.get_data.side_effect = original_operation
        clock.now += 1
        await refresh_and_settle(coordinator)
        assert coordinator._last_sample_rejection is None
        assert coordinator._consecutive_no_sample == 0
        assert coordinator.runtime_status["data_buf"]["recent_samples"][-1]["accepted"]
        await coordinator.async_shutdown()
    asyncio.run(scenario())
