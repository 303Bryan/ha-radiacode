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


@pytest.mark.parametrize("payload", [b"", struct.pack("<I", 1), struct.pack("<II", 0, 0)])
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
            assert c.accumulated_dose_fresh
            # A dose read cannot keep the replayed dose/count rates alive.
            assert not c.measurement_available
            assert c._last_fresh_monotonic == 1000
            assert not c.last_update_success
        finally:
            await c.async_shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("bad_value", [None, math.nan, math.inf, -1, TimeoutError("lost response")])
def test_failed_register_reads_expire_dose_without_hiding_live_rates(make_coordinator, protocol, clock, bad_value):
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
            assert not c.accumulated_dose_fresh
            assert "accumulated_dose" in c.runtime_status["maintenance"]["errors"]
            c._client.get_accumulated_dose.side_effect = None
            c._client.get_accumulated_dose.return_value = 1.3
            clock.now += 60
            await refresh_and_settle(c)
            assert c.accumulated_dose_fresh
            assert c.data.sensors.accumulated_dose == 1.3
            assert "accumulated_dose" not in c.runtime_status["maintenance"]["errors"]
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
