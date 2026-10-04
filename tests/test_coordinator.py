"""Coordinator lifecycle tests with gated BLE operations and a deterministic clock.

The HA boundary is isolated here. tests/ha_runtime exercises the real scheduler
and entity state writes; neither suite substitutes for physical device testing.
"""

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


def readings(protocol, **values):
    """Build a full data frame, allowing omitted/empty device records."""
    return protocol.RadiaCodeData(**{
        "dose_rate": None, "count_rate": None, "accumulated_dose": None,
        "battery": None, "temperature": None, **values,
    })


@pytest.fixture
def coordinator_module(monkeypatch, protocol):
    """Import the actual coordinator with only its HA boundary replaced."""
    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, logger, **kwargs):
            self.hass = hass
            self.data = None
            self.last_update_success = True
            self.last_exception = None
            self.update_interval = kwargs["update_interval"]
            self.config_entry = kwargs["config_entry"]
            self.refresh_count = 0
            self.listener_updates = 0

        async def async_refresh(self):
            try:
                self.data = await self._async_update_data()
            except UpdateFailed as err:
                self.last_update_success = False
                self.last_exception = err
            else:
                self.last_update_success = True
                self.last_exception = None
            self.async_update_listeners()

        async def async_request_refresh(self):
            self.refresh_count += 1
            await self.async_refresh()

        def async_update_listeners(self):
            self.listener_updates += 1

        def async_set_updated_data(self, data):
            raise AssertionError("maintenance must not reset primary scheduling/success")

        async def async_shutdown(self):
            pass

    def module(name, **attributes):
        result = ModuleType(name)
        result.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    module("bleak", __path__=[])
    module("bleak.backends", __path__=[])
    module("bleak.backends.device", BLEDevice=object)
    module("homeassistant", __path__=[])
    module("homeassistant.components", __path__=[])
    module(
        "homeassistant.components.bluetooth",
        async_ble_device_from_address=Mock(return_value=object()),
        async_last_service_info=Mock(return_value=None),
    )
    module("homeassistant.config_entries", ConfigEntry=SimpleNamespace)
    module("homeassistant.core", HomeAssistant=object)
    module("homeassistant.helpers", __path__=[])
    module(
        "homeassistant.helpers.device_registry",
        CONNECTION_BLUETOOTH="bluetooth", DeviceInfo=dict,
        async_get=Mock(return_value=SimpleNamespace(
            async_get_device_by_identifier=Mock(return_value=None),
            async_update_device=Mock(),
        )),
    )
    module(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=DataUpdateCoordinator, UpdateFailed=UpdateFailed,
    )
    package = "radiacode_coordinator_test"
    root = Path(__file__).resolve().parent.parent / "custom_components" / "radiacode"
    module(package, __path__=[str(root)])
    module(
        f"{package}.radiacode_ble", __path__=[],
        RadiaCodeBLEClient=SimpleNamespace,
        RadiaCodeInitError=type("InitError", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, f"{package}.radiacode_ble.protocol", protocol)
    for name in ("const", "coordinator"):
        spec = importlib.util.spec_from_file_location(f"{package}.{name}", root / f"{name}.py")
        loaded = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, loaded)
        spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def clock(monkeypatch, coordinator_module):
    clock = SimpleNamespace(now=1000.0)
    # Keep asyncio's actual monotonic clock running for gated task scheduling.
    monkeypatch.setattr(coordinator_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    return clock


@pytest.fixture
def make_coordinator(coordinator_module, protocol, clock):
    """Use actual advancing measurement timestamps, not implicit fresh caches."""
    class FakeHass:
        def __init__(self):
            self.tasks = []

        def async_create_background_task(self, coro, name, *, eager_start):
            assert eager_start is False
            task = asyncio.create_task(coro, name=name)
            self.tasks.append(task)
            return task

    def make(**options):
        entry = SimpleNamespace(
            entry_id="radiacode-entry", data={"address": "AA:BB:CC:DD:EE:FF"},
            options={"spectrum_interval": 60, **options},
        )
        coordinator = coordinator_module.RadiaCodeCoordinator(FakeHass(), entry)

        async def measurement():
            return readings(protocol,
                dose_rate=0.12, count_rate=3.0, accumulated_dose=2.0,
                battery=98.0,
                measurement_time=datetime(2026, 1, 1) + timedelta(seconds=clock.now),
                measurement_type="RealTimeData", measurement_flags=0x40,
            )

        async def connect(device):
            coordinator._client.is_connected = True

        async def disconnect():
            coordinator._client.is_connected = False

        coordinator._client = SimpleNamespace(
            is_connected=True, spectrum_format_version=0,
            spectrum_format_source="direct_spectrum", transport_diagnostics={"generation": 1},
            get_data=AsyncMock(side_effect=measurement),
            get_settings=AsyncMock(return_value=protocol.RadiaCodeSettings(sound_on=True)),
            get_diagnostics=AsyncMock(return_value=protocol.RadiaCodeDiagnostics(sipm_bias_mv=27000)),
            get_temperature=AsyncMock(return_value=25.0),
            get_spectrum=AsyncMock(return_value=protocol.Spectrum(
                duration_s=60, a0=-2.0, a1=3.0, a2=0.001, counts=[1] * 1024,
            )),
            get_serial_number=AsyncMock(return_value="RC-103-000123"),
            get_firmware_version=AsyncMock(return_value="4.14"),
            get_sfr_file=AsyncMock(return_value=""),
            connect=AsyncMock(side_effect=connect),
            disconnect=AsyncMock(side_effect=disconnect),
            write_vsfr=AsyncMock(return_value=True), reset_spectrum=AsyncMock(return_value=True),
        )
        return coordinator

    return make


async def settle(coordinator):
    """Await the one worker, including its done callback."""
    if coordinator._maintenance_task is not None:
        await coordinator._maintenance_task
    await asyncio.sleep(0)


async def refresh_and_settle(coordinator):
    await coordinator.async_refresh()
    await settle(coordinator)
    return coordinator.data


def test_primary_publishes_before_blocked_spectrum_and_worker_uses_latest_snapshot(
    make_coordinator, clock, protocol,
):
    async def scenario():
        coordinator = make_coordinator()
        entered, release = asyncio.Event(), asyncio.Event()
        result = coordinator._client.get_spectrum.return_value

        async def blocked_spectrum():
            entered.set()
            await release.wait()
            return result

        coordinator._client.get_spectrum.side_effect = blocked_spectrum
        await coordinator.async_refresh()
        assert coordinator.data.sensors.dose_rate == 0.12
        assert coordinator.data.spectrum is None
        assert coordinator.last_update_success is True
        await entered.wait()
        worker = coordinator._maintenance_task
        first_time = coordinator.data.sensors.measurement_time
        clock.now += 5
        await coordinator.async_refresh()
        assert coordinator.data.sensors.measurement_time > first_time
        assert coordinator._maintenance_task is worker
        assert len(coordinator.hass.tasks) == 1
        latest_time = coordinator.data.sensors.measurement_time
        release.set()
        await settle(coordinator)
        assert coordinator.data.spectrum is result
        assert coordinator.data.sensors.measurement_time == latest_time
        assert coordinator._last_fresh_monotonic == clock.now
        assert coordinator.last_update_success is True
        await coordinator.async_shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("poll_interval", [5, 300])
def test_optional_reads_use_elapsed_minutes(make_coordinator, clock, poll_interval):
    async def scenario():
        coordinator = make_coordinator(poll_interval=poll_interval, spectrum_interval=0)
        first = await refresh_and_settle(coordinator)
        clock.now = 1059
        for _ in range(20):
            snapshot = await refresh_and_settle(coordinator)
            assert snapshot.settings is first.settings
            assert snapshot.diagnostics is first.diagnostics
        for name in ("get_settings", "get_diagnostics", "get_temperature"):
            assert getattr(coordinator._client, name).await_count == 1
        clock.now = 1060
        await refresh_and_settle(coordinator)
        for name in ("get_settings", "get_diagnostics", "get_temperature"):
            assert getattr(coordinator._client, name).await_count == 2
        assert coordinator._client.get_data.await_count == 22
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_fresh_rare_temperature_defers_fallback_but_cached_display_does_not(
    make_coordinator, clock, protocol,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        clock.now = 1000
        fresh = readings(protocol,
            dose_rate=0.1, count_rate=3.0, temperature=0.0,
            measurement_time=datetime(2026, 1, 1), measurement_type="RealTimeData",
        )
        coordinator._client.get_data.side_effect = None
        coordinator._client.get_data.return_value = fresh
        await refresh_and_settle(coordinator)
        assert coordinator.data.sensors.temperature == 0.0
        coordinator._client.get_temperature.assert_not_awaited()
        coordinator._client.get_data.return_value = readings(protocol)
        clock.now = 1005
        await refresh_and_settle(coordinator)
        assert coordinator._next_temperature_read == 1060
        clock.now = 1060
        await refresh_and_settle(coordinator)
        coordinator._client.get_temperature.assert_awaited_once()
        assert coordinator.data.sensors.temperature == 25.0
        assert coordinator.last_update_success is False  # radiation aged out
        await coordinator.async_shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_read", ["get_settings", "get_diagnostics", "get_temperature"])
def test_optional_failures_keep_cache_and_reconnect_without_early_retry(
    make_coordinator, clock, failed_read,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        client = coordinator._client
        first = await refresh_and_settle(coordinator)

        async def fail_and_drop():
            client.is_connected = False
            raise RuntimeError("optional read dropped transport")

        getattr(client, failed_read).side_effect = fail_and_drop
        clock.now = 1060
        snapshot = await refresh_and_settle(coordinator)
        assert snapshot.settings is first.settings
        assert snapshot.diagnostics is first.diagnostics
        assert snapshot.sensors.temperature == first.sensors.temperature
        assert coordinator.last_update_success is True
        assert coordinator.last_error is None
        assert client.is_connected is False
        clock.now = 1065
        await refresh_and_settle(coordinator)
        client.connect.assert_awaited_once()
        assert getattr(client, failed_read).await_count == 2
        clock.now = 1119
        await refresh_and_settle(coordinator)
        assert getattr(client, failed_read).await_count == 2
        clock.now = 1120
        await refresh_and_settle(coordinator)
        assert getattr(client, failed_read).await_count == 3
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_identity_failure_uses_cooldown_across_reconnect(make_coordinator, clock):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        client = coordinator._client

        async def fail_and_drop():
            client.is_connected = False
            raise RuntimeError("serial unavailable")

        client.get_serial_number.side_effect = fail_and_drop
        await refresh_and_settle(coordinator)
        assert coordinator.data.sensors.dose_rate == 0.12
        assert coordinator.runtime_status["maintenance"]["errors"]["identity"] == "serial unavailable"
        clock.now = 1005
        await refresh_and_settle(coordinator)
        client.connect.assert_awaited_once()
        assert client.get_serial_number.await_count == 1
        clock.now = 1060
        await refresh_and_settle(coordinator)
        assert client.get_serial_number.await_count == 2
        client.get_firmware_version.assert_not_awaited()
        client.get_sfr_file.assert_not_awaited()
        await coordinator.async_shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("replayed", [False, True])
def test_empty_or_replayed_records_do_not_renew_freshness(make_coordinator, clock, protocol, replayed):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        first = await refresh_and_settle(coordinator)
        coordinator._client.get_data.side_effect = None
        coordinator._client.get_data.return_value = (
            first.sensors if replayed else readings(protocol)
        )
        clock.now = 1059.9
        current = await refresh_and_settle(coordinator)
        assert coordinator.last_update_success is True
        assert current.sensors.measurement_time == first.sensors.measurement_time
        assert coordinator._last_fresh_monotonic == 1000
        assert coordinator.runtime_status["freshness"]["using_cached_measurement"] is True
        clock.now = 1060
        await refresh_and_settle(coordinator)
        assert coordinator.last_update_success is False
        assert "stale" in coordinator.last_error
        assert coordinator.runtime_status["freshness"]["fresh"] is False
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_initial_empty_buffer_does_not_claim_radiation_available(make_coordinator, protocol):
    async def scenario():
        coordinator = make_coordinator()
        coordinator._client.get_data.side_effect = None
        coordinator._client.get_data.return_value = readings(protocol)
        await refresh_and_settle(coordinator)
        assert coordinator.data is None
        assert coordinator.last_update_success is False
        assert coordinator.last_error == "Waiting for a fresh radiation measurement"
        assert coordinator._last_spectrum is not None
        assert coordinator.runtime_status["freshness"]["age_seconds"] is None
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_zero_pair_is_fresh_and_slow_poll_gets_three_interval_grace(
    make_coordinator, clock, protocol,
):
    async def scenario():
        coordinator = make_coordinator(poll_interval=300, spectrum_interval=0)
        first = await refresh_and_settle(coordinator)
        zero = readings(protocol,
            dose_rate=0.0, count_rate=0.0,
            measurement_time=first.sensors.measurement_time + timedelta(seconds=300),
            measurement_type="RawData", measurement_flags=None,
        )
        coordinator._client.get_data.side_effect = None
        coordinator._client.get_data.return_value = zero
        clock.now = 1300
        await refresh_and_settle(coordinator)
        assert coordinator.data.sensors.dose_rate == 0.0
        assert coordinator.data.sensors.count_rate == 0.0
        assert coordinator.data.sensors.hardness is None
        assert coordinator.data.sensors.measurement_type == "RawData"
        assert coordinator.runtime_status["freshness"]["grace_seconds"] == 900
        assert coordinator._last_fresh_monotonic == 1300
        assert coordinator.runtime_status["freshness"]["sample_progress"] == {
            "device_seconds": 300, "receipt_seconds": 300,
        }
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_filtered_pair_does_not_renew_freshness(make_coordinator, clock, protocol):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        first = await refresh_and_settle(coordinator)
        coordinator._client.get_data.side_effect = None
        coordinator._client.get_data.return_value = readings(protocol,
            dose_rate=40000, count_rate=1000000,
            measurement_time=first.sensors.measurement_time + timedelta(seconds=5),
            measurement_type="RealTimeData",
        )
        clock.now += 5
        await refresh_and_settle(coordinator)
        assert coordinator.data.sensors.dose_rate == first.sensors.dose_rate
        assert coordinator.data.sensors.measurement_time == first.sensors.measurement_time
        assert coordinator._last_fresh_monotonic == 1000
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_background_publication_preserves_failed_radiation_and_freshness(make_coordinator, clock):
    async def scenario():
        coordinator = make_coordinator()
        entered, release = asyncio.Event(), asyncio.Event()
        result = coordinator._client.get_spectrum.return_value

        async def gated():
            entered.set()
            await release.wait()
            return result

        coordinator._client.get_spectrum.side_effect = gated
        await coordinator.async_refresh()
        await entered.wait()
        coordinator._client.get_data.side_effect = None
        coordinator._client.get_data.return_value = coordinator.data.sensors
        clock.now = 1060
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        error = coordinator.last_error
        release.set()
        await settle(coordinator)
        assert coordinator.data.spectrum is result
        assert coordinator.last_update_success is False
        assert coordinator.last_error == error
        assert coordinator._last_fresh_monotonic == 1000
        await coordinator.async_shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["async_user_disconnect", "async_shutdown"])
def test_disconnect_cancels_and_awaits_worker_before_transport_release(make_coordinator, action):
    async def scenario():
        coordinator = make_coordinator()
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def blocked():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def disconnect():
            assert cancelled.is_set()
            coordinator._client.is_connected = False

        coordinator._client.get_spectrum.side_effect = blocked
        coordinator._client.disconnect.side_effect = disconnect
        await coordinator.async_refresh()
        await entered.wait()
        worker = coordinator._maintenance_task
        updates = coordinator.listener_updates
        await getattr(coordinator, action)()
        assert worker.cancelled()
        assert coordinator._maintenance_task is None
        assert coordinator._maintenance_phase is None
        assert coordinator._last_spectrum is None
        assert coordinator.listener_updates == updates + 2 * (action == "async_user_disconnect")
        coordinator._client.disconnect.assert_awaited_once()
        await coordinator.async_refresh()
        assert len(coordinator.hass.tasks) == 1

    asyncio.run(scenario())


def test_worker_yields_next_operation_to_active_primary_poll(make_coordinator):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        entered, release = asyncio.Event(), asyncio.Event()
        result = coordinator._client.get_settings.return_value

        async def gate_settings():
            entered.set()
            await release.wait()
            return result

        coordinator._client.get_settings.side_effect = gate_settings
        await coordinator.async_refresh()
        await entered.wait()
        coordinator._primary_idle.clear()
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        coordinator._client.get_diagnostics.assert_not_awaited()
        coordinator._primary_idle.set()
        await settle(coordinator)
        coordinator._client.get_diagnostics.assert_awaited_once()
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_cancel_before_worker_start_and_user_reconnect_have_no_orphan_task(make_coordinator, clock):
    async def scenario():
        coordinator = make_coordinator()
        await coordinator.async_refresh()
        worker = coordinator._maintenance_task
        # The worker is deliberately non-eager and has not issued any command.
        coordinator._client.get_serial_number.assert_not_awaited()
        await coordinator.async_user_disconnect()
        assert worker.cancelled()
        assert coordinator._maintenance_task is None
        coordinator._client.get_spectrum.assert_not_awaited()
        clock.now += 5
        await coordinator.async_user_reconnect()
        await settle(coordinator)
        assert coordinator.last_update_success is True
        assert coordinator.is_ble_connected is True
        coordinator._client.connect.assert_awaited_once()
        assert coordinator.data.spectrum is not None
        assert len(coordinator.hass.tasks) == 2
        assert all(task.done() for task in coordinator.hass.tasks)
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_spectrum_failures_back_off_and_recover_without_losing_cached_snapshot(
    make_coordinator, clock, protocol,
):
    async def scenario():
        coordinator = make_coordinator()
        client = coordinator._client
        cached = (await refresh_and_settle(coordinator)).spectrum
        client.get_spectrum.side_effect = RuntimeError("incomplete spectrum")
        clock.now = 1060
        assert (await refresh_and_settle(coordinator)).spectrum is cached
        assert coordinator.last_update_success is True
        assert coordinator.last_error is None
        assert coordinator.spectrum_status["retry_in_seconds"] == 300
        clock.now = 1359
        await refresh_and_settle(coordinator)
        assert client.get_spectrum.await_count == 2
        clock.now = 1360
        await refresh_and_settle(coordinator)
        assert coordinator.spectrum_status["retry_in_seconds"] == 600
        recovered = protocol.Spectrum(duration_s=120, a0=0, a1=3, a2=0, counts=[2] * 1024)
        client.get_spectrum.side_effect = None
        client.get_spectrum.return_value = recovered
        clock.now = 1960
        assert (await refresh_and_settle(coordinator)).spectrum is recovered
        assert coordinator.spectrum_status["last_error"] is None
        assert coordinator.spectrum_status["retry_in_seconds"] == 60
        assert coordinator._spectrum_retry_delay == 300
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_manual_current_spectrum_updates_sensor_but_accumulated_stays_separate(make_coordinator, protocol):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        await refresh_and_settle(coordinator)
        client = coordinator._client
        client.get_spectrum.assert_not_awaited()
        current = await coordinator.async_get_spectrum()
        assert coordinator.data.spectrum is current
        accumulated = protocol.Spectrum(duration_s=600, a0=0, a1=3, a2=0, counts=[9] * 1024)
        client.get_spectrum.return_value = accumulated
        assert await coordinator.async_get_spectrum(accumulated=True) is accumulated
        assert coordinator.data.spectrum is current
        assert coordinator._last_spectrum is current
        assert coordinator.spectrum_status["retry_in_seconds"] is None
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_setting_write_refreshes_settings_on_next_maintenance(make_coordinator, clock, protocol):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        await refresh_and_settle(coordinator)
        clock.now = 1005
        coordinator._client.get_settings.return_value = protocol.RadiaCodeSettings(sound_on=False)
        await coordinator.async_write_setting(protocol.VSFR.SOUND_ON, 0)
        await settle(coordinator)
        coordinator._client.write_vsfr.assert_awaited_once_with(protocol.VSFR.SOUND_ON, 0)
        assert coordinator.refresh_count == 1
        assert coordinator.data.settings.sound_on is False
        assert coordinator._next_settings_read == 1065
        await coordinator.async_shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("modern", [True, False])
def test_device_identity_uses_scoped_registry_api_with_legacy_fallback(
    make_coordinator, coordinator_module, modern,
):
    coordinator = make_coordinator()
    registry = SimpleNamespace(
        async_get_device=Mock(return_value=SimpleNamespace(id="legacy-device")),
        async_update_device=Mock(),
    )
    if modern:
        registry.async_get_device_by_identifier = Mock(return_value=SimpleNamespace(id="scoped-device"))
    coordinator_module.dr.async_get.return_value = registry
    asyncio.run(coordinator._fetch_device_identity())
    if modern:
        registry.async_get_device_by_identifier.assert_called_once_with(
            ("radiacode", "AA:BB:CC:DD:EE:FF"), "radiacode-entry",
        )
        registry.async_get_device.assert_not_called()
    else:
        registry.async_get_device.assert_called_once_with(
            identifiers={("radiacode", "AA:BB:CC:DD:EE:FF")},
        )
    registry.async_update_device.assert_called_once_with(
        "scoped-device" if modern else "legacy-device", serial_number="RC-103-000123", sw_version="4.14",
    )


def test_user_disabled_poll_does_no_reads(make_coordinator):
    async def scenario():
        coordinator = make_coordinator()
        coordinator._user_disconnected = True
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        assert "disabled by user" in coordinator.last_error
        for name in ("get_data", "get_settings", "get_diagnostics", "get_temperature", "get_spectrum", "get_serial_number"):
            getattr(coordinator._client, name).assert_not_awaited()
        assert coordinator._maintenance_task is None

    asyncio.run(scenario())


@pytest.mark.parametrize("action,args", [
    ("async_write_setting", (0x0522, 0)), ("async_reset_dose", ()),
    ("async_reset_spectrum", ()), ("async_get_spectrum", ()),
])
def test_user_disabled_actions_reject_connected_transport(make_coordinator, coordinator_module, action, args):
    coordinator = make_coordinator()
    coordinator._user_disconnected = True
    with pytest.raises(coordinator_module.UpdateFailed, match="disabled by user"):
        asyncio.run(getattr(coordinator, action)(*args))
    coordinator._client.write_vsfr.assert_not_awaited()
    coordinator._client.reset_spectrum.assert_not_awaited()
    coordinator._client.get_spectrum.assert_not_awaited()


def test_runtime_diagnostics_are_bounded_and_label_advertisement_source(make_coordinator, coordinator_module, clock):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        coordinator._client.is_connected = False
        coordinator_module.bluetooth.async_last_service_info.return_value = SimpleNamespace(
            source="advertising-proxy", rssi=-61,
        )
        await refresh_and_settle(coordinator)
        coordinator_module.bluetooth.async_last_service_info.assert_called_once_with(
            coordinator.hass, coordinator.address, connectable=True,
        )
        assert coordinator.runtime_status["bluetooth"] == {
            "advertisement_source_at_connect": "advertising-proxy",
            "advertisement_rssi_at_connect": -61,
        }
        for _ in range(30):
            clock.now += 5
            await refresh_and_settle(coordinator)
        assert len(coordinator.runtime_status["recent_phases"]) == 20
        assert coordinator.transport_status == {"generation": 1}
        assert "channels" not in coordinator.spectrum_status
        assert coordinator.spectrum_status["format_source"] == "direct_spectrum"
        await coordinator.async_shutdown()

    asyncio.run(scenario())
