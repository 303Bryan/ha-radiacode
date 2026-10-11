"""Spectrum status publication through the actual Home Assistant state machine.

BLE replies are controlled; these tests verify entity/coordinator behavior,
not physical proxy throughput. The dependency-free suite skips this module.
"""

import asyncio
import datetime as dt
import logging
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

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


class CountedChannels(list):
    """Observe traversal of the source histogram after it is cached."""

    iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()


@pytest_asyncio.fixture
async def spectrum_runtime(tmp_path):
    """Publish real HA states with primary polling and spectrum reads isolated."""
    hass = HomeAssistant(str(tmp_path))
    entry = ConfigEntry(
        data={"address": "AA:BB:CC:DD:EE:FF", "name": "Test detector"},
        options={"poll_interval": 5, "spectrum_interval": 60},
        domain="radiacode", title="Test detector", version=1, minor_version=1,
        source="user", unique_id="AA:BB:CC:DD:EE:FF",
        discovery_keys=MappingProxyType({}), subentries_data=None,
    )
    coordinator = RadiaCodeCoordinator(hass, entry)
    sample_number = 0

    async def next_measurement():
        nonlocal sample_number
        sample_number += 1
        return RadiaCodeData(
            dose_rate=0.12, count_rate=12.0, accumulated_dose=None,
            battery=80.0, temperature=None,
            measurement_time=dt.datetime(2026, 1, 1, 12)
            + timedelta(seconds=sample_number),
            measurement_type="RealTimeData", measurement_flags=0x0040,
        )

    counts = CountedChannels([1] * 1024)
    client = SimpleNamespace(
        is_connected=True, spectrum_format_version=1,
        spectrum_format_source="configuration", transport_diagnostics={},
        configuration_retry_due=False,
        get_data=AsyncMock(side_effect=next_measurement),
        get_settings=AsyncMock(return_value=RadiaCodeSettings()),
        get_diagnostics=AsyncMock(return_value=RadiaCodeDiagnostics()),
        get_temperature=AsyncMock(return_value=25.0),
        get_spectrum=AsyncMock(return_value=Spectrum(
            duration_s=60, a0=-2, a1=3, a2=0.001, counts=counts,
        )),
        reset_spectrum=AsyncMock(return_value=True),
        disconnect=AsyncMock(),
    )
    coordinator._client = client
    coordinator._serial_number = "TEST-DEVICE"
    coordinator._fw_version = "4.14"
    for name in ("settings", "diagnostics", "temperature", "spectrum"):
        setattr(coordinator, "_next_" + name + "_read", float("inf"))
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
        await coordinator.async_refresh()
        yield hass, coordinator, client, dose, spectrum, counts
    finally:
        await coordinator.async_shutdown()
        for entity in (dose, spectrum):
            await entity.async_remove(force_remove=True)
        await hass.async_stop(force=True)


async def settle_maintenance(coordinator):
    if (task := coordinator._maintenance_task) is not None:
        await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)


def assert_primary_unchanged(hass, coordinator, dose, receipt, timer):
    assert coordinator._last_fresh_monotonic == receipt
    assert coordinator._unsub_refresh is timer
    assert coordinator.last_update_success
    assert coordinator.measurement_available
    assert dose.available
    assert hass.states.get(dose.entity_id).state == "0.12"


@pytest.mark.asyncio
async def test_automatic_pause_and_manual_recovery_publish_only_changed_metadata(
    spectrum_runtime,
):
    hass, coordinator, client, dose, spectrum, counts = spectrum_runtime
    cached = await coordinator.async_get_spectrum()
    original = hass.states.get(spectrum.entity_id)
    acquired_at = original.attributes["acquired_at"]
    assert original.state == "1024"
    assert dt.datetime.fromisoformat(acquired_at).utcoffset() == timedelta(0)
    assert coordinator.spectrum_status["acquired_at"] == acquired_at
    assert original.attributes["automatic_paused"] is False
    channels = spectrum.extra_state_attributes["channels"]
    traversals = counts.iterations
    client.get_spectrum.side_effect = RuntimeError("incomplete spectrum")

    with patch.object(
        spectrum, "async_write_ha_state", wraps=spectrum.async_write_ha_state,
    ) as writes:
        for attempt in range(1, 4):
            coordinator._next_spectrum_read = 0.0
            await coordinator.async_refresh()
            receipt = coordinator._last_fresh_monotonic
            timer = coordinator._unsub_refresh
            await settle_maintenance(coordinator)
            state = hass.states.get(spectrum.entity_id)
            assert state.state == "1024"
            assert state.attributes["acquired_at"] == acquired_at
            assert state.attributes["automatic_paused"] is (attempt == 3)
            assert coordinator.data.spectrum is cached
            assert spectrum.extra_state_attributes["channels"] is channels
            assert counts.iterations == traversals
            assert_primary_unchanged(hass, coordinator, dose, receipt, timer)
        # Failure counts and unchanged primary polls are not spectrum updates.
        assert writes.call_count == 1
        assert client.get_spectrum.await_count == 4
        for _ in range(3):
            await coordinator.async_refresh()
            await settle_maintenance(coordinator)
        assert writes.call_count == 1
        assert client.get_spectrum.await_count == 4
        assert counts.iterations == traversals

        # A successful read of the same snapshot still supplies a new time.
        client.get_spectrum.side_effect = None
        receipt = coordinator._last_fresh_monotonic
        timer = coordinator._unsub_refresh
        assert await coordinator.async_get_spectrum() is cached
        state = hass.states.get(spectrum.entity_id)
        assert state.state == "1024"
        assert state.attributes["automatic_paused"] is False
        assert dt.datetime.fromisoformat(state.attributes["acquired_at"]) > (
            dt.datetime.fromisoformat(acquired_at)
        )
        assert coordinator.spectrum_status["acquired_at"] == state.attributes["acquired_at"]
        assert spectrum.extra_state_attributes["channels"] is channels
        assert counts.iterations == traversals
        assert writes.call_count == 2
        assert_primary_unchanged(hass, coordinator, dose, receipt, timer)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_snapshot", (False, True))
async def test_reset_publishes_unknown_time_and_resumes_with_or_without_histogram(
    spectrum_runtime, monkeypatch, has_snapshot,
):
    hass, coordinator, client, dose, spectrum, counts = spectrum_runtime
    if has_snapshot:
        await coordinator.async_get_spectrum()
    else:
        initial = hass.states.get(spectrum.entity_id)
        assert initial.state == "unknown"
        assert initial.attributes["acquired_at"] is None
        assert initial.attributes["automatic_paused"] is False
    traversals = counts.iterations
    client.get_spectrum.side_effect = RuntimeError("incomplete spectrum")
    for _ in range(3):
        with pytest.raises(UpdateFailed, match="incomplete spectrum"):
            await coordinator.async_get_spectrum()
    paused = hass.states.get(spectrum.entity_id)
    assert paused.state == ("1024" if has_snapshot else "unknown")
    assert paused.attributes["automatic_paused"] is True
    assert (paused.attributes["acquired_at"] is not None) is has_snapshot
    assert counts.iterations == traversals

    # Observe the explicit reset publication before its requested reacquisition.
    refresh = AsyncMock()
    monkeypatch.setattr(coordinator, "async_request_refresh", refresh)
    receipt = coordinator._last_fresh_monotonic
    timer = coordinator._unsub_refresh
    await coordinator.async_reset_spectrum()
    client.reset_spectrum.assert_awaited_once_with()
    refresh.assert_awaited_once_with()
    reset = hass.states.get(spectrum.entity_id)
    assert reset.state == "unknown"
    assert reset.attributes["acquired_at"] is None
    assert reset.attributes["automatic_paused"] is False
    assert "channels" not in reset.attributes
    assert coordinator.data.spectrum is None
    assert coordinator.spectrum_status["acquired_at"] is None
    assert coordinator.spectrum_status["automatic_paused"] is False
    assert counts.iterations == traversals
    assert_primary_unchanged(hass, coordinator, dose, receipt, timer)


@pytest.mark.asyncio
async def test_accumulated_read_preserves_current_histogram_time_and_pause(
    spectrum_runtime,
):
    hass, coordinator, client, dose, spectrum, counts = spectrum_runtime
    current = await coordinator.async_get_spectrum()
    client.get_spectrum.side_effect = RuntimeError("incomplete current spectrum")
    for _ in range(3):
        with pytest.raises(UpdateFailed):
            await coordinator.async_get_spectrum()
    before = hass.states.get(spectrum.entity_id)
    assert before.attributes["automatic_paused"] is True
    attributes = spectrum.extra_state_attributes
    acquired_at = before.attributes["acquired_at"]
    traversals = counts.iterations
    receipt = coordinator._last_fresh_monotonic
    timer = coordinator._unsub_refresh
    accumulated = Spectrum(600, -2, 3, 0.001, [9] * 1024)
    client.get_spectrum.side_effect = None
    client.get_spectrum.return_value = accumulated

    with patch.object(
        spectrum, "async_write_ha_state", wraps=spectrum.async_write_ha_state,
    ) as writes:
        assert await coordinator.async_get_spectrum(accumulated=True) is accumulated
        writes.assert_not_called()
    client.get_spectrum.assert_awaited_with(accumulated=True)
    after = hass.states.get(spectrum.entity_id)
    assert after.state == "1024"
    assert after.attributes["acquired_at"] == acquired_at
    assert after.attributes["automatic_paused"] is True
    assert coordinator.data.spectrum is current
    assert coordinator.spectrum_status["acquired_at"] == acquired_at
    assert coordinator.spectrum_status["automatic_paused"] is True
    assert spectrum.extra_state_attributes is attributes
    assert counts.iterations == traversals
    assert_primary_unchanged(hass, coordinator, dose, receipt, timer)
