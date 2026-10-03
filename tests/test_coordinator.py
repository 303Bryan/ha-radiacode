"""Coordinator behavior tests with a fake BLE device and isolated HA boundary.

These exercise the real polling/action methods with a deterministic clock;
they do not simulate Home Assistant's scheduler or a physical BLE transport.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def coordinator_module(monkeypatch, protocol):
    """Import the real coordinator without starting HA or loading bleak."""

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, logger, **kwargs):
            self.hass = hass
            self.data = None
            self.last_update_success = True
            self.update_interval = kwargs["update_interval"]
            self.refresh_count = 0
            self.listener_updates = 0

        async def async_request_refresh(self):
            self.refresh_count += 1
            try:
                self.data = await self._async_update_data()
            except UpdateFailed:
                self.last_update_success = False
                raise
            self.last_update_success = True

        def async_update_listeners(self):
            self.listener_updates += 1

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
        RadiaCodeBLEClient=SimpleNamespace, RadiaCodeInitError=type("InitError", (Exception,), {}),
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
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: clock.now)
    return clock


@pytest.fixture
def make_coordinator(coordinator_module, protocol, clock):
    """Build a connected coordinator with independently observable BLE calls."""
    def make(**options):
        entry = SimpleNamespace(
            entry_id="radiacode-entry", data={"address": "AA:BB:CC:DD:EE:FF"},
            options={"spectrum_interval": 60, **options},
        )
        coordinator = coordinator_module.RadiaCodeCoordinator(object(), entry)
        coordinator._client = SimpleNamespace(
            is_connected=True, spectrum_format_version=0,
            get_data=AsyncMock(return_value=protocol.RadiaCodeData(
                dose_rate=0.12, count_rate=3.0, accumulated_dose=2.0,
                battery=98.0, temperature=25.0,
            )),
            get_settings=AsyncMock(return_value=protocol.RadiaCodeSettings(sound_on=True)),
            get_diagnostics=AsyncMock(return_value=protocol.RadiaCodeDiagnostics(sipm_bias_mv=27000)),
            get_spectrum=AsyncMock(return_value=protocol.Spectrum(
                duration_s=60, a0=-2.0, a1=3.0, a2=0.001, counts=[1] * 1024,
            )),
            get_serial_number=AsyncMock(return_value="RC-103-000123"),
            get_firmware_version=AsyncMock(return_value="4.14"),
            get_sfr_file=AsyncMock(return_value=""),
            connect=AsyncMock(), disconnect=AsyncMock(),
            write_vsfr=AsyncMock(return_value=True),
            reset_spectrum=AsyncMock(return_value=True),
        )
        return coordinator

    return make


def update(coordinator):
    return asyncio.run(coordinator._async_update_data())


@pytest.mark.parametrize("poll_interval", [5, 300])
def test_settings_and_diagnostics_use_elapsed_minutes(make_coordinator, clock, poll_interval):
    coordinator = make_coordinator(poll_interval=poll_interval, spectrum_interval=0)
    client = coordinator._client
    first = update(coordinator)

    clock.now = 1059.0
    for _ in range(20):
        snapshot = update(coordinator)
        assert snapshot.settings is first.settings
        assert snapshot.diagnostics is first.diagnostics
    assert client.get_settings.await_count == 1
    assert client.get_diagnostics.await_count == 1

    clock.now = 1060.0
    update(coordinator)
    assert client.get_settings.await_count == 2
    assert client.get_diagnostics.await_count == 2
    assert client.get_data.await_count == 22


def test_failed_slow_reads_keep_cache_without_fast_retry(make_coordinator, clock):
    coordinator = make_coordinator(spectrum_interval=0)
    client = coordinator._client
    first = update(coordinator)
    client.get_settings.side_effect = RuntimeError("settings unavailable")
    client.get_diagnostics.side_effect = RuntimeError("diagnostics unavailable")

    clock.now = 1060.0
    snapshot = update(coordinator)
    clock.now = 1065.0
    update(coordinator)

    assert snapshot.settings is first.settings
    assert snapshot.diagnostics is first.diagnostics
    assert client.get_settings.await_count == 2
    assert client.get_diagnostics.await_count == 2
    assert snapshot.sensors.dose_rate == 0.12


@pytest.mark.parametrize("failed_read", ["get_settings", "get_diagnostics"])
def test_reconnecting_after_optional_failure_preserves_retry_deadline(
    make_coordinator, clock, failed_read,
):
    coordinator = make_coordinator(spectrum_interval=0)
    client = coordinator._client

    async def fail_and_drop_connection():
        client.is_connected = False
        raise RuntimeError("optional read disconnected the device")

    async def reconnect(ble_device):
        client.is_connected = True

    getattr(client, failed_read).side_effect = fail_and_drop_connection
    client.connect.side_effect = reconnect
    first = update(coordinator)
    assert first.sensors.dose_rate == 0.12
    assert client.is_connected is False

    # Radiation polling reconnects promptly, but reconnecting must not
    # override the failing optional read's once-per-minute retry deadline.
    clock.now = 1005.0
    reconnected = update(coordinator)
    client.connect.assert_awaited_once()
    assert reconnected.sensors.dose_rate == 0.12
    assert client.is_connected is True
    assert getattr(client, failed_read).await_count == 1

    clock.now = 1059.0
    update(coordinator)
    assert getattr(client, failed_read).await_count == 1

    clock.now = 1060.0
    update(coordinator)
    assert getattr(client, failed_read).await_count == 2


def test_reconnecting_after_identity_failure_preserves_retry_deadline(make_coordinator, clock):
    coordinator = make_coordinator(spectrum_interval=0)
    client = coordinator._client

    async def fail_and_drop_connection():
        client.is_connected = False
        raise RuntimeError("serial read disconnected the device")

    async def reconnect(ble_device):
        client.is_connected = True

    client.get_serial_number.side_effect = fail_and_drop_connection
    client.connect.side_effect = reconnect
    assert update(coordinator).sensors.dose_rate == 0.12
    assert client.is_connected is False

    clock.now = 1005.0
    assert update(coordinator).sensors.dose_rate == 0.12
    client.connect.assert_awaited_once()
    assert client.is_connected is True
    assert client.get_serial_number.await_count == 1

    clock.now = 1059.0
    update(coordinator)
    assert client.get_serial_number.await_count == 1

    clock.now = 1060.0
    assert update(coordinator).sensors.dose_rate == 0.12
    assert client.get_serial_number.await_count == 2
    client.get_firmware_version.assert_not_awaited()
    client.get_sfr_file.assert_not_awaited()


def test_setting_write_refreshes_settings_immediately(make_coordinator, clock, protocol):
    coordinator = make_coordinator(spectrum_interval=0)
    client = coordinator._client
    update(coordinator)
    clock.now = 1005.0
    client.get_settings.return_value = protocol.RadiaCodeSettings(sound_on=False)

    asyncio.run(coordinator.async_write_setting(protocol.VSFR.SOUND_ON, 0))

    client.write_vsfr.assert_awaited_once_with(protocol.VSFR.SOUND_ON, 0)
    assert coordinator.refresh_count == 1
    assert coordinator.data.settings.sound_on is False
    assert client.get_settings.await_count == 2
    assert client.get_diagnostics.await_count == 1
    assert coordinator._next_settings_read == 1065.0


def test_spectrum_failures_preserve_snapshot_back_off_and_recover(
    make_coordinator, clock, protocol,
):
    coordinator = make_coordinator()
    client = coordinator._client
    cached = update(coordinator).spectrum
    client.get_spectrum.side_effect = RuntimeError("incomplete spectrum")

    clock.now = 1060.0
    first_failure = update(coordinator)
    assert first_failure.spectrum is cached
    assert first_failure.sensors.dose_rate == 0.12
    assert coordinator.last_error is None
    assert coordinator.spectrum_status["last_error"] == "incomplete spectrum"
    assert coordinator.spectrum_status["retry_in_seconds"] == 300.0
    assert "channels" not in coordinator.spectrum_status

    clock.now = 1359.0
    assert update(coordinator).spectrum is cached
    assert client.get_spectrum.await_count == 2

    clock.now = 1360.0
    assert update(coordinator).spectrum is cached
    assert coordinator.spectrum_status["retry_in_seconds"] == 600.0
    assert client.get_spectrum.await_count == 3

    recovered = protocol.Spectrum(duration_s=120, a0=0, a1=3, a2=0, counts=[2] * 1024)
    client.get_spectrum.side_effect = None
    client.get_spectrum.return_value = recovered
    clock.now = 1960.0
    assert update(coordinator).spectrum is recovered
    assert coordinator.spectrum_status["last_error"] is None
    assert coordinator.spectrum_status["retry_in_seconds"] == 60.0
    assert coordinator._spectrum_retry_delay == 300.0
    assert client.get_data.await_count == 5


def test_zero_interval_disables_automatic_but_allows_requested_spectrum(make_coordinator, clock):
    coordinator = make_coordinator(spectrum_interval=0)
    update(coordinator)
    clock.now = 10000.0
    update(coordinator)
    coordinator._client.get_spectrum.assert_not_awaited()
    assert coordinator.spectrum_status["retry_in_seconds"] is None

    spectrum = asyncio.run(coordinator.async_get_spectrum(accumulated=True))
    coordinator._client.get_spectrum.assert_awaited_once_with(accumulated=True)
    assert len(spectrum.counts) == 1024


def test_spectrum_status_describes_cached_snapshot_without_copying_histogram(
    make_coordinator, clock, protocol,
):
    class NoIterationList(list):
        def __iter__(self):
            raise AssertionError("diagnostic status must not traverse the histogram")

    coordinator = make_coordinator()
    counts = NoIterationList([1] * 1024)
    cached = protocol.Spectrum(duration_s=120, a0=0, a1=3, a2=0, counts=counts)
    coordinator._last_spectrum = cached
    coordinator._last_spectrum_error = "incomplete spectrum"
    coordinator._next_spectrum_read = clock.now + 300.0

    assert coordinator.spectrum_status == {
        "poll_interval": 60.0,
        "format_version": 0,
        "last_error": "incomplete spectrum",
        "channel_count": 1024,
        "duration_s": 120,
        "truncated": False,
        "retry_in_seconds": 300.0,
    }
    assert coordinator._last_spectrum is cached
    assert coordinator._last_spectrum.counts is counts


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
        "scoped-device" if modern else "legacy-device",
        serial_number="RC-103-000123", sw_version="4.14",
    )


def test_user_disabled_poll_does_no_reads(make_coordinator, coordinator_module):
    coordinator = make_coordinator()
    coordinator._user_disconnected = True

    with pytest.raises(coordinator_module.UpdateFailed, match="disabled by user"):
        update(coordinator)

    for name in ("get_data", "get_settings", "get_diagnostics", "get_spectrum", "get_serial_number"):
        getattr(coordinator._client, name).assert_not_awaited()


@pytest.mark.parametrize("phase", [
    "get_serial_number", "get_settings", "get_diagnostics", "get_spectrum",
])
def test_user_disable_during_poll_prevents_remaining_optional_reads(
    make_coordinator, coordinator_module, phase,
):
    coordinator = make_coordinator()
    client = coordinator._client
    phase_result = getattr(client, phase).return_value

    async def disable_while_reading():
        # A user action sets this before awaiting disconnect. The transport
        # can still report connected while its disconnect is in progress.
        coordinator._user_disconnected = True
        return phase_result

    getattr(client, phase).side_effect = disable_while_reading
    with pytest.raises(coordinator_module.UpdateFailed, match="disabled by user"):
        update(coordinator)

    if phase != "get_spectrum":
        client.get_spectrum.assert_not_awaited()
    if phase in ("get_serial_number", "get_settings"):
        client.get_diagnostics.assert_not_awaited()
    if phase == "get_serial_number":
        client.get_settings.assert_not_awaited()
        client.get_firmware_version.assert_not_awaited()
        client.get_sfr_file.assert_not_awaited()


@pytest.mark.parametrize("action,args", [
    ("async_write_setting", (0x0522, 0)),
    ("async_reset_dose", ()),
    ("async_reset_spectrum", ()),
    ("async_get_spectrum", ()),
])
def test_user_disabled_actions_reject_connected_transport(
    make_coordinator, coordinator_module, action, args,
):
    coordinator = make_coordinator()
    coordinator._user_disconnected = True

    with pytest.raises(coordinator_module.UpdateFailed, match="disabled by user"):
        asyncio.run(getattr(coordinator, action)(*args))

    coordinator._client.write_vsfr.assert_not_awaited()
    coordinator._client.reset_spectrum.assert_not_awaited()
    coordinator._client.get_spectrum.assert_not_awaited()
