"""Sparse dose records must not cause periodic alarm outages or mask stale rates."""
import asyncio
import datetime as dt
import math
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_client import client_module, attached_client  # noqa: F401
from test_coordinator import (  # noqa: F401
    clock, coordinator_module, make_coordinator, readings, refresh_and_settle,
)
from test_sensor import sensor_module  # noqa: F401

BASE = dt.datetime(2026, 11, 2)


def test_sparse_dose_records_survive_50_minute_gaps_with_continuous_live_samples(make_coordinator, protocol, clock):
    """Synthetic timing replay; no captured identity or raw payload is embedded."""
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._client.get_accumulated_dose.side_effect = ValueError("VSFR read failed: retcode=0")
        dose_times = {0, 3005, 6005}
        async def sample():
            elapsed = clock.now - 1000
            stamp = BASE + dt.timedelta(seconds=elapsed)
            return readings(protocol, dose_rate=.12, count_rate=3,
                measurement_time=stamp, measurement_type="RealTimeData",
                accumulated_dose=.2 + elapsed / 10000 if elapsed in dose_times else None,
                accumulated_dose_time=stamp if elapsed in dose_times else None)
        c._client.get_data.side_effect = sample
        try:
            for elapsed in range(0, 10805, 5):
                clock.now = 1000 + elapsed
                await refresh_and_settle(c)
                assert c.accumulated_dose_fresh
                assert c._dose_observed == 1000 + max(t for t in dose_times if t <= elapsed)
            assert c._client.get_accumulated_dose.await_count == 3
            assert c.accumulated_dose_status["register_read_paused"]
            assert c.accumulated_dose_status["measurement_age_seconds"] == 4795
            assert c.accumulated_dose_status["freshness_limit_seconds"] is None
            assert c.measurement_available
            # A real outage invalidates the old total even after rates return.
            clock.now += 60
            await refresh_and_settle(c)
            assert c.measurement_available
            assert not c.accumulated_dose_fresh
            assert c.accumulated_dose_status["observation_required"]
        finally:
            await c.async_shutdown()
    asyncio.run(scenario())


def test_dose_cache_cannot_outlive_live_measurement_lease(make_coordinator, protocol, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._client.get_accumulated_dose.return_value = 1.2
        try:
            await refresh_and_settle(c)
            c._client.get_data.side_effect = None
            c._client.get_data.return_value = readings(protocol)
            clock.now += 60
            await refresh_and_settle(c)
            assert not c.measurement_available
            assert not c.accumulated_dose_fresh
            assert c._dose_observed == clock.now  # even a successful read cannot mask this
        finally:
            await c.async_shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("expire_callback", [True, False])
def test_rates_cannot_revive_pre_outage_dose_until_new_observation(
    make_coordinator, protocol, clock, expire_callback,
):
    """Cover both independent expiry and a late timer racing poll completion."""
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._schedule_maintenance = lambda: None
        c._client.get_data.side_effect = None
        c._client.get_data.return_value = readings(protocol, dose_rate=.12, count_rate=3,
            measurement_time=BASE, accumulated_dose=2, accumulated_dose_time=BASE)
        try:
            await c.async_refresh()
            observed = c._dose_observed
            clock.now += 60
            if expire_callback:
                c._cancel_measurement_expiry()
                c._expire_measurement()
                assert not c.accumulated_dose_fresh
            # Advancing rates with a replayed dose record cannot renew the total.
            c._client.get_data.return_value.measurement_time = BASE + dt.timedelta(seconds=60)
            await c.async_refresh()
            assert c.measurement_available
            assert not c.accumulated_dose_fresh
            assert c._dose_observed == observed
            clock.now += 5
            c._client.get_data.return_value.measurement_time += dt.timedelta(seconds=5)
            c._client.get_data.return_value.accumulated_dose_time += dt.timedelta(seconds=65)
            c._client.get_data.return_value.accumulated_dose = 2.1
            await c.async_refresh()
            assert c.accumulated_dose_fresh
            assert c.data.sensors.accumulated_dose == 2.1
        finally:
            await c.async_shutdown()
    asyncio.run(scenario())


def test_brief_reconnect_preserves_dose_but_user_disconnect_requires_observation(make_coordinator, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._client.get_accumulated_dose.return_value = 1.2
        try:
            await refresh_and_settle(c)
            c._dose_read_failures = 3
            c._client.is_connected = False
            clock.now += 25
            await refresh_and_settle(c)
            assert c.accumulated_dose_fresh
            assert c._dose_observed == 1000
            clock.now += 5
            await c.async_user_disconnect()
            clock.now += 5
            await c.async_user_reconnect()
            await refresh_and_settle(c)
            assert c.measurement_available
            assert not c.accumulated_dose_fresh
            assert c.accumulated_dose_status["measurement_age_seconds"] == 35
            await c.async_reset_dose()
            assert c.accumulated_dose_fresh
            assert c.data.sensors.accumulated_dose == 0
        finally:
            await c.async_shutdown()
    asyncio.run(scenario())


def test_inflight_pre_outage_register_read_cannot_revalidate_dose(make_coordinator, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._schedule_maintenance = lambda: None
        await c.async_refresh()
        entered, release = asyncio.Event(), asyncio.Event()
        async def old_read():
            entered.set()
            await release.wait()
            return 5.0
        c._client.get_accumulated_dose.side_effect = old_read
        pending = asyncio.create_task(c._refresh_accumulated_dose())
        await entered.wait()
        clock.now += 65
        await c.async_refresh()
        release.set()
        await pending
        c._publish_maintenance_cache()
        assert c.measurement_available
        assert not c.accumulated_dose_fresh
        assert c._dose_observed == 1000
        # A confirmed read after the outage restores the total.
        c._client.get_accumulated_dose.side_effect = None
        c._client.get_accumulated_dose.return_value = 5.1
        await c._refresh_accumulated_dose()
        assert c.accumulated_dose_fresh
        await c.async_shutdown()
    asyncio.run(scenario())


def test_status_only_register_rejection_is_not_a_truncated_value(protocol):
    # Both public RC-102/4.14 evidence and the later private RC-103/4.15
    # debug capture identify a zero status word, not a successful zero dose.
    with pytest.raises(ValueError, match="retcode=0"):
        protocol.parse_vsfr_read_response(struct.pack("<I", 0), protocol.VSFR.DS_uR)


def test_dose_read_pause_survives_reconnect_but_firmware_change_retries(make_coordinator, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        try:
            for offset in (0, 60, 120):
                clock.now = 1000 + offset
                await refresh_and_settle(c)
            assert c.accumulated_dose_status["register_read_paused"]
            c._client.is_connected = False
            clock.now += 5
            await refresh_and_settle(c)
            assert c._client.get_accumulated_dose.await_count == 3
            assert c.accumulated_dose_status["register_read_paused"]
            # A confirmed build/version change is the only in-session reset.
            c._client.get_firmware_version.return_value = "4.15"
            c._client.get_accumulated_dose.return_value = .4
            c._client.is_connected = False
            clock.now += 5
            await refresh_and_settle(c)
            assert c._client.get_accumulated_dose.await_count == 4
            assert not c.accumulated_dose_status["register_read_paused"]
            assert c._dose_read_failures == 0
        finally:
            await c.async_shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("native,expected", [(0, 0.0), (12345, 123.45), (999999999, 9999999.99)])
def test_dose_register_uses_individual_read_and_native_units(client_module, native, expected):
    async def scenario():
        client, _ = attached_client(client_module)
        client._execute = AsyncMock(return_value=struct.pack("<II", 1, native))
        assert await client.get_accumulated_dose() == expected
        client._execute.assert_awaited_once_with(
            client_module.CMD.RD_VIRT_SFR, struct.pack("<I", client_module.VSFR.DS_uR))
        await client.disconnect()
    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [b"", struct.pack("<I", 0), struct.pack("<I", 1), struct.pack("<II", 0, 0), struct.pack("<III", 1, 10, 20)])
def test_failed_dose_register_is_not_zero(client_module, payload):
    async def scenario():
        client, _ = attached_client(client_module)
        client._execute = AsyncMock(return_value=payload)
        with pytest.raises(ValueError):
            await client.get_accumulated_dose()
        await client.disconnect()
    asyncio.run(scenario())


def test_sparse_rare_data_and_unchanged_dose_stay_available_for_half_hour(make_coordinator, protocol, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._client.get_accumulated_dose.return_value = 0.0
        # Every poll has a live pair but no RareData, matching the supplied tail.
        async def sample():
            return readings(protocol, dose_rate=.12, count_rate=12,
                measurement_time=BASE + dt.timedelta(seconds=clock.now), measurement_type="RealTimeData")
        c._client.get_data.side_effect = sample
        try:
            for elapsed in range(0, 1801, 5):
                clock.now = 1000 + elapsed
                await refresh_and_settle(c)
                assert c.accumulated_dose_fresh
                assert c.data.sensors.accumulated_dose == 0.0
                assert c.measurement_available
                assert c.accumulated_dose_status["measurement_source"] == "dose_register"
                assert c.accumulated_dose_status["measurement_age_seconds"] < 60
            assert c._client.get_accumulated_dose.await_count == 31
        finally:
            await c.async_shutdown()
    asyncio.run(scenario())


def test_fresh_rare_dose_defers_read_but_replay_does_not(make_coordinator, protocol, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._client.get_data.side_effect = None
        c._client.get_data.return_value = readings(protocol, dose_rate=.12, count_rate=12,
            measurement_time=BASE, measurement_type="RealTimeData",
            accumulated_dose=2, accumulated_dose_time=BASE)
        c._client.get_accumulated_dose.return_value = 2.5
        try:
            await refresh_and_settle(c)
            c._client.get_accumulated_dose.assert_not_awaited()
            assert c.accumulated_dose_status["measurement_source"] == "rare_data"
            clock.now += 59
            await refresh_and_settle(c)
            c._client.get_accumulated_dose.assert_not_awaited()
            clock.now += 1
            await refresh_and_settle(c)
            assert c.data.sensors.accumulated_dose == 2.5
            assert not c.accumulated_dose_fresh
            # A dose read cannot keep the replayed dose/count rates alive.
            assert not c.measurement_available
            assert c._last_fresh_monotonic == 1000
            assert not c.last_update_success
        finally:
            await c.async_shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("bad_value", [None, math.nan, math.inf, -1, TimeoutError("lost response")])
def test_failed_register_reads_pause_and_cannot_renew_dose(make_coordinator, protocol, clock, bad_value):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._client.get_accumulated_dose.return_value = 1.2
        try:
            await refresh_and_settle(c)
            assert c.accumulated_dose_fresh
            if isinstance(bad_value, Exception):
                c._client.get_accumulated_dose.side_effect = bad_value
            else:
                c._client.get_accumulated_dose.return_value = bad_value
            for seconds in (60, 120, 180):
                clock.now = 1000 + seconds
                await refresh_and_settle(c)
                assert c.data.sensors.accumulated_dose == 1.2
                assert c._dose_observed == 1000
                assert c.measurement_available
            assert c.accumulated_dose_status["register_read_paused"]
            clock.now = 2800
            await refresh_and_settle(c)
            assert not c.accumulated_dose_fresh
            assert "accumulated_dose" in c.runtime_status["maintenance"]["errors"]
            c._client.get_accumulated_dose.side_effect = None
            c._client.get_accumulated_dose.return_value = 1.3
            clock.now += 60
            await refresh_and_settle(c)
            assert not c.accumulated_dose_fresh
            assert c._client.get_accumulated_dose.await_count == 4
            # A new device dose record recovers without retrying a failed register.
            c._client.get_data.side_effect = None
            c._client.get_data.return_value = readings(protocol, dose_rate=.12, count_rate=3,
                measurement_time=BASE, accumulated_dose=1.3, accumulated_dose_time=BASE)
            await refresh_and_settle(c)
            assert c.accumulated_dose_fresh
            assert c.data.sensors.accumulated_dose == 1.3
            await c.async_user_disconnect()
            assert not c.accumulated_dose_fresh
        finally:
            await c.async_shutdown()
    asyncio.run(scenario())


def test_inflight_dose_read_cannot_undo_successful_reset(make_coordinator, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._schedule_maintenance = lambda: None
        await c.async_refresh()
        entered, release = asyncio.Event(), asyncio.Event()
        async def old_read():
            entered.set()
            await release.wait()
            return 5.0
        c._client.get_accumulated_dose.side_effect = old_read
        pending = asyncio.create_task(c._refresh_accumulated_dose())
        await entered.wait()
        clock.now += 1
        await c.async_reset_dose()
        release.set()
        await pending
        c._publish_maintenance_cache()
        assert c.data.sensors.accumulated_dose == 0
        assert c.accumulated_dose_fresh
        assert c.accumulated_dose_status["measurement_source"] == "dose_reset"
        await c.async_shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,key,name", [
    ("dose_rate", "radiation_alarm", "Dose Rate Alarm"),
    ("count_rate", "count_rate_alarm", "Count Rate Alarm"),
    ("accumulated_dose", "accumulated_dose_alarm", "Accumulated Dose Alarm"),
])
def test_alarm_names_preserve_identity_and_default_thresholds(sensor_module, protocol, kind, key, name):
    c = SimpleNamespace(measurement_available=True, accumulated_dose_fresh=True,
        accumulated_dose_status={}, data=SimpleNamespace(
            sensors=readings(protocol, dose_rate=.2, count_rate=15, accumulated_dose=0),
            settings=protocol.RadiaCodeSettings(dr_alarm_level1=40, dr_alarm_level2=120,
                cr_alarm_level1=200, cr_alarm_level2=600,
                ds_alarm_level1=999999999, ds_alarm_level2=999999999)))
    entity = sensor_module.RadiaCodeRadiationAlarmSensor(c,
        SimpleNamespace(data={"address": "AA", "name": "Detector"}), kind)
    assert entity._attr_name == name
    assert entity._attr_unique_id == f"AA-{key}"
    assert entity.available and entity.native_value == "No Alarm"
    if kind == "accumulated_dose":
        assert entity.extra_state_attributes["level1_threshold_usv"] == 9999999.99
