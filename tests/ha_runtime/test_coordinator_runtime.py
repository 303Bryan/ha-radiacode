"""Acquisition publication through the actual HA coordinator and entities.

These tests require the pinned runtime in requirements-ha-runtime.txt. BLE
acquisition remains controlled; this suite does not simulate proxy throughput.
The small stdlib-only test environment skips this module.
"""

import asyncio
import datetime as dt
import logging
import json
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.radiacode.coordinator import RadiaCodeCoordinator
from custom_components.radiacode.radiacode_ble.protocol import (
    RadiaCodeData,
    RadiaCodeDiagnostics,
    RadiaCodeSettings,
    Spectrum,
)
from custom_components.radiacode.sensor import (
    SENSOR_DESCRIPTIONS,
    RadiaCodeSensor,
    RadiaCodeSpectrumSensor,
)
from custom_components.radiacode.diagnostics import async_get_config_entry_diagnostics


@pytest_asyncio.fixture
async def runtime(tmp_path, monkeypatch):
    """Use real coordinator/entity lifecycles without starting Bluetooth."""
    hass = HomeAssistant(str(tmp_path))
    entry = ConfigEntry(
        data={"address": "AA:BB:CC:DD:EE:FF", "name": "Test detector"},
        options={"poll_interval": 5, "spectrum_interval": 60},
        domain="radiacode", title="Test detector", version=1, minor_version=1,
        source="user", unique_id="AA:BB:CC:DD:EE:FF",
        discovery_keys=MappingProxyType({}), subentries_data=None,
    )
    coordinator = RadiaCodeCoordinator(hass, entry)
    sample_time = dt.datetime(2026, 1, 1, 12)
    client = SimpleNamespace(
        is_connected=True, spectrum_format_version=1,
        spectrum_format_source="validated_payload", transport_diagnostics={},
        get_data=AsyncMock(return_value=RadiaCodeData(
            dose_rate=0.12, count_rate=12.0, battery=80.0,
            accumulated_dose=None, temperature=None,
            measurement_time=sample_time, measurement_type="RealTimeData",
            measurement_flags=0x0040,
        )),
        get_temperature=AsyncMock(return_value=25.0),
        get_settings=AsyncMock(return_value=RadiaCodeSettings()),
        get_diagnostics=AsyncMock(return_value=RadiaCodeDiagnostics()),
        get_serial_number=AsyncMock(return_value="TEST-DEVICE"),
        get_firmware_version=AsyncMock(return_value="4.14"),
        get_spectrum=AsyncMock(return_value=Spectrum(
            duration_s=60, a0=-2, a1=3, a2=0.001, counts=[1] * 1024,
        )),
        connect=AsyncMock(), disconnect=AsyncMock(),
    )
    coordinator._client = client
    # Identity lookup uses the registry and is unrelated to publication.
    coordinator._serial_number = "TEST-DEVICE"
    coordinator._fw_version = "4.14"
    monkeypatch.setattr(
        "custom_components.radiacode.coordinator.bluetooth.async_last_service_info",
        lambda *args, **kwargs: None,
    )
    platform = EntityPlatform(
        hass=hass, logger=logging.getLogger(__name__), domain="sensor",
        platform_name="radiacode", platform=None,
        scan_interval=timedelta(seconds=30), entity_namespace=None,
    )
    dose = RadiaCodeSensor(coordinator, entry, SENSOR_DESCRIPTIONS[0])
    spectrum = RadiaCodeSpectrumSensor(coordinator, entry)
    for entity, entity_id in (
        (dose, "sensor.test_detector_dose_rate"),
        (spectrum, "sensor.test_detector_spectrum"),
    ):
        entity.entity_id = entity_id
        entity.add_to_platform_start(hass, platform, None)
        await entity.add_to_platform_finish()
    try:
        yield hass, coordinator, client, dose, spectrum
    finally:
        await coordinator.async_shutdown()
        for entity in (dose, spectrum):
            await entity.async_remove(force_remove=True)
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_radiation_reaches_ha_before_blocked_spectrum(runtime):
    hass, coordinator, client, dose, spectrum = runtime
    entered = asyncio.Event()
    release = asyncio.Event()

    async def read_spectrum(*args, **kwargs):
        entered.set()
        await release.wait()
        return Spectrum(60, -2, 3, 0.001, [2] * 1024)

    client.get_spectrum.side_effect = read_spectrum
    await asyncio.wait_for(coordinator.async_refresh(), timeout=1)
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert hass.states.get(dose.entity_id).state == "0.12"
    assert hass.states.get(spectrum.entity_id).state == "unknown"
    assert coordinator.last_update_success
    assert coordinator._maintenance_task is not None
    scheduled_refresh = coordinator._unsub_refresh
    assert scheduled_refresh is not None
    release.set()
    await asyncio.wait_for(coordinator._maintenance_task, timeout=1)
    state = hass.states.get(spectrum.entity_id)
    assert state.state == "2048"
    assert state.attributes["channel_count"] == 1024
    assert state.attributes["channels"] == [2] * 1024
    assert coordinator._unsub_refresh is scheduled_refresh


@pytest.mark.asyncio
async def test_optional_publication_preserves_failed_primary(runtime):
    hass, coordinator, client, dose, spectrum = runtime
    entered = asyncio.Event()
    release = asyncio.Event()

    async def read_spectrum(*args, **kwargs):
        entered.set()
        await release.wait()
        return Spectrum(60, -2, 3, 0.001, [2] * 1024)

    client.get_spectrum.side_effect = read_spectrum
    await coordinator.async_refresh()
    await asyncio.wait_for(entered.wait(), timeout=1)
    coordinator.async_set_update_error(UpdateFailed("primary data unavailable"))
    assert not coordinator.last_update_success
    assert hass.states.get(dose.entity_id).state == "0.12"
    assert hass.states.get(dose.entity_id).attributes["using_cached_measurement"]
    coordinator._last_fresh_monotonic -= coordinator._freshness_grace
    coordinator._cancel_measurement_expiry()
    coordinator._expire_measurement()
    assert hass.states.get(dose.entity_id).state == "unavailable"
    release.set()
    await asyncio.wait_for(coordinator._maintenance_task, timeout=1)
    assert not coordinator.last_update_success
    assert hass.states.get(dose.entity_id).state == "unavailable"
    assert coordinator.data.spectrum is not None


@pytest.mark.asyncio
async def test_shutdown_cancels_real_background_worker(runtime):
    _, coordinator, client, _, _ = runtime
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def read_spectrum(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client.get_spectrum.side_effect = read_spectrum
    await coordinator.async_refresh()
    await asyncio.wait_for(entered.wait(), timeout=1)
    worker = coordinator._maintenance_task
    await asyncio.wait_for(coordinator.async_shutdown(), timeout=1)
    assert cancelled.is_set()
    assert worker.done()
    assert coordinator._maintenance_task is None
    client.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_measurement_lease_expires_during_blocked_real_refresh(runtime):
    hass, coordinator, client, dose, _ = runtime
    coordinator._freshness_grace = 0.2
    coordinator._spectrum_interval = 0
    await coordinator.async_refresh()
    if coordinator._maintenance_task is not None:
        await coordinator._maintenance_task
    entered, release = asyncio.Event(), asyncio.Event()
    sample = client.get_data.return_value
    async def blocked_poll():
        entered.set()
        await release.wait()
        return sample
    client.get_data.side_effect = blocked_poll
    pending = asyncio.create_task(coordinator.async_refresh())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert hass.states.get(dose.entity_id).state == "0.12"
        await asyncio.sleep(0.25)
        assert not pending.done()
        assert hass.states.get(dose.entity_id).state == "unavailable"
    finally:
        release.set()
        await asyncio.wait_for(pending, timeout=1)


@pytest.mark.asyncio
async def test_failed_real_acquisition_keeps_lease_without_renewing_it(runtime):
    hass, coordinator, _, dose, _ = runtime
    coordinator._freshness_grace = 0.2
    coordinator._spectrum_interval = 0
    await coordinator.async_refresh()
    if coordinator._maintenance_task is not None:
        await coordinator._maintenance_task
    receipt = coordinator._last_fresh_monotonic
    coordinator.async_set_update_error(UpdateFailed("transient BLE drop"))
    assert not coordinator.last_update_success
    assert hass.states.get(dose.entity_id).state == "0.12"
    coordinator.async_set_update_error(UpdateFailed("still reconnecting"))
    assert coordinator._last_fresh_monotonic == receipt
    await asyncio.sleep(0.25)
    assert hass.states.get(dose.entity_id).state == "unavailable"


@pytest.mark.asyncio
async def test_user_disconnect_cancels_worker_and_suspends_real_refresh(runtime):
    _, coordinator, client, _, _ = runtime
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def read_spectrum(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client.get_spectrum.side_effect = read_spectrum
    await coordinator.async_refresh()
    await asyncio.wait_for(entered.wait(), timeout=1)
    worker = coordinator._maintenance_task
    polls_before_disconnect = client.get_data.await_count
    await asyncio.wait_for(coordinator.async_user_disconnect(), timeout=1)
    assert cancelled.is_set()
    assert worker.done()
    assert coordinator._maintenance_task is None
    assert coordinator.user_disconnected
    client.disconnect.assert_awaited()
    await coordinator.async_refresh()
    assert client.get_data.await_count == polls_before_disconnect
    assert coordinator._maintenance_task is None


@pytest.mark.asyncio
async def test_manual_spectrum_read_updates_real_entity(runtime):
    hass, coordinator, client, _, spectrum = runtime
    # Leave optional acquisition disabled for this manual-action test.
    coordinator._next_spectrum_read = float("inf")
    await coordinator.async_refresh()
    worker = coordinator._maintenance_task
    if worker is not None:
        await worker
    client.get_spectrum.return_value = Spectrum(120, -2, 3, 0.001, [3] * 1024)
    await coordinator.async_get_spectrum()
    assert hass.states.get(spectrum.entity_id).state == "3072"
    assert hass.states.get(spectrum.entity_id).attributes["duration_s"] == 120


@pytest.mark.asyncio
async def test_optional_completion_keeps_newer_primary_snapshot(runtime):
    hass, coordinator, client, dose, _ = runtime
    entered = asyncio.Event()
    release = asyncio.Event()

    async def read_spectrum(*args, **kwargs):
        entered.set()
        await release.wait()
        return Spectrum(60, -2, 3, 0.001, [2] * 1024)

    client.get_spectrum.side_effect = read_spectrum
    await coordinator.async_refresh()
    await asyncio.wait_for(entered.wait(), timeout=1)
    later = dt.datetime(2026, 1, 1, 12, 0, 5)
    client.get_data.return_value = RadiaCodeData(
        dose_rate=0.2, count_rate=20.0, accumulated_dose=None,
        battery=None, temperature=None, measurement_time=later,
        measurement_type="RealTimeData", measurement_flags=0x0040,
    )
    await coordinator.async_refresh()
    assert hass.states.get(dose.entity_id).state == "0.2"
    fresh_receipt = coordinator._last_fresh_monotonic
    release.set()
    await asyncio.wait_for(coordinator._maintenance_task, timeout=1)
    assert hass.states.get(dose.entity_id).state == "0.2"
    assert coordinator.data.sensors.measurement_time == later
    assert coordinator._last_fresh_monotonic == fresh_receipt


@pytest.mark.asyncio
async def test_downloadable_diagnostics_serialize_and_redact_sources(runtime):
    hass, coordinator, client, _, _ = runtime
    await coordinator.async_refresh()
    if coordinator._maintenance_task is not None:
        await coordinator._maintenance_task
    client.transport_diagnostics = {
        "connection": {
            "connection_request_source": "11:22:33:44:55:66",
            "connection_request_source_name": "Private proxy name",
            "connection_request_scanner_source": "22:33:44:55:66:77",
        },
        "recent_commands": [{"target": "SPECTRUM", "received_body_bytes": 1254}],
    }
    hass.data["radiacode"] = {coordinator._entry_id: coordinator}
    result = await async_get_config_entry_diagnostics(hass, coordinator.config_entry)
    serialized = json.dumps(result)
    assert "Private proxy name" not in serialized
    assert "11:22:33:44:55:66" not in serialized
    assert "22:33:44:55:66:77" not in serialized
    assert "AA:BB:CC:DD:EE:FF" not in serialized
    assert result["sensors"]["measurement_time"] == "2026-01-01T12:00:00"
    assert result["transport"]["recent_commands"][0]["received_body_bytes"] == 1254


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,lead", [("RawData", 128), ("DoseRateDB", 43)])
async def test_live_updates_remain_published_past_lease_with_future_mixed_records(
    runtime, monkeypatch, kind, lead,
):
    from custom_components.radiacode import coordinator as coordinator_module
    from custom_components.radiacode.radiacode_ble.protocol import (
        DoseRateDB, RawData, RealTimeData, extract_sensor_values,
    )

    hass, coordinator, client, dose, _ = runtime
    receipt_clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(
        coordinator_module, "time", SimpleNamespace(monotonic=lambda: receipt_clock.now),
    )
    coordinator._spectrum_interval = 0
    base = dt.datetime(2026, 1, 1, 12)
    for step in range(16):
        receipt_clock.now = 1000 + step * 5
        timestamp = base + timedelta(seconds=step * 5)
        live = RealTimeData(
            timestamp, 8.93 + step * 0.01, 1,
            0.00001268 + step * 0.00000001, 1, 0x2040,
        )
        records = [live]
        # Anonymized observed offsets; isolated RT polls between mixed replies
        # reproduced RC2's successful-read-but-rejected-live-update failure.
        if step in (0, 8):
            future = timestamp + timedelta(seconds=lead)
            if kind == "RawData":
                records.append(RawData(future, 16.0, 0.00009238))
            else:
                records.append(DoseRateDB(future, 100, 8.9, 0.0000127, 1))
        client.get_data.return_value = extract_sensor_values(records)
        await coordinator.async_refresh()
        if coordinator._maintenance_task is not None:
            await coordinator._maintenance_task
        state = hass.states.get(dose.entity_id)
        assert float(state.state) == pytest.approx(live.dose_rate * 10_000)
        assert coordinator.data.sensors.measurement_time == timestamp
        assert coordinator.data.sensors.measurement_type == "RealTimeData"
        assert coordinator.last_update_success
        assert coordinator._last_sample_rejection is None
        assert coordinator._last_fresh_monotonic == receipt_clock.now
    assert receipt_clock.now - 1000 > coordinator._freshness_grace
    assert dose.available


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,lead", [("RawData", 128), ("DoseRateDB", 43)])
async def test_fallback_to_earlier_live_time_keeps_real_entity_current(
    runtime, monkeypatch, kind, lead,
):
    from custom_components.radiacode import coordinator as coordinator_module

    hass, coordinator, client, dose, _ = runtime
    receipt_clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(
        coordinator_module, "time", SimpleNamespace(monotonic=lambda: receipt_clock.now),
    )
    coordinator._spectrum_interval = 0
    base = dt.datetime(2026, 1, 1, 12)
    client.get_data.return_value = RadiaCodeData(
        dose_rate=0.1268, count_rate=8.93, accumulated_dose=None,
        battery=None, temperature=None,
        measurement_time=base + timedelta(seconds=lead), measurement_type=kind,
    )
    await coordinator.async_refresh()
    if coordinator._maintenance_task is not None:
        await coordinator._maintenance_task
    assert hass.states.get(dose.entity_id).state == "0.1268"
    receipt_clock.now += 5
    timestamp = base + timedelta(seconds=5)
    client.get_data.return_value = RadiaCodeData(
        dose_rate=0.13, count_rate=9.0, accumulated_dose=None,
        battery=None, temperature=None, measurement_time=timestamp,
        measurement_type="RealTimeData", measurement_flags=0x2040,
    )
    await coordinator.async_refresh()
    assert hass.states.get(dose.entity_id).state == "0.13"
    assert not hass.states.get(dose.entity_id).attributes["using_cached_measurement"]
    assert coordinator.data.sensors.measurement_time == timestamp
    assert coordinator.last_update_success
    assert dose.available
    assert coordinator._last_sample_progress == {
        "device_seconds": None, "receipt_seconds": 5,
    }
