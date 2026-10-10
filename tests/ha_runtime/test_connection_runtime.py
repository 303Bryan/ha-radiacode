"""Connection lifecycle through HA's real Bluetooth catcher and connector.

The real BluetoothManager setup installs Home Assistant's client wrappers.
Only adapter discovery and the final GATT backend are controlled: these tests
check library compatibility and cancellation, not a physical proxy or radio.
"""

import asyncio
import importlib.util
import logging
import struct
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

connector = pytest.importorskip("bleak_retry_connector")
habluetooth = pytest.importorskip("habluetooth")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

from bleak.backends.device import BLEDevice
from habluetooth import wrappers
from habluetooth.central_manager import CentralBluetoothManager


@pytest_asyncio.fixture
async def connection_runtime(monkeypatch):
    """Install the actual catcher before importing the integration's client."""
    previous_manager = CentralBluetoothManager.manager
    adapters = types.SimpleNamespace(refresh=AsyncMock(), adapters={})
    manager = habluetooth.BluetoothManager(bluetooth_adapters=adapters)
    # Avoid opening a Linux management socket in this library compatibility test.
    monkeypatch.setattr("habluetooth.manager.IS_LINUX", False)
    habluetooth.set_manager(manager)
    await manager.async_setup()

    package_name = "_radiacode_connection_runtime"
    ble_dir = Path(__file__).resolve().parents[2] / "custom_components/radiacode/radiacode_ble"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ble_dir)]
    monkeypatch.setitem(sys.modules, package_name, package)
    loaded = {}
    for name in ("protocol", "client"):
        qualified = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(qualified, ble_dir / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qualified, module)
        spec.loader.exec_module(module)
        loaded[name] = module
    module = loaded["client"]
    module._CONNECT_TIMEOUT = 0.04
    module._DISCONNECT_TIMEOUT = 0.04

    device = BLEDevice("AA:BB:CC:DD:EE:FF", "Test detector", {"source": "test-proxy"})
    scanner = types.SimpleNamespace(
        adapter_idx=None, source="test-proxy", name="Test proxy", _clients=set(),
        _add_connecting=lambda address: None,
        _finished_connecting=lambda address, connected: None,
    )
    # Use the installed wrapper's actual constructor, connect and disconnect;
    # route its final backend to a controlled proxy without registering hardware.
    wrapper_manager = types.SimpleNamespace(
        shutdown=False, async_scanner_by_source=lambda source: scanner,
        async_release_connection_slot=lambda device: None,
    )
    monkeypatch.setattr(wrappers, "get_manager", lambda: wrapper_manager)
    monkeypatch.setattr(wrappers._LOGGER, "level", logging.WARNING)
    transports = []

    class Services:
        def __iter__(self):
            return iter([types.SimpleNamespace(uuid=module.SERVICE_UUID)])

        def get_characteristic(self, characteristic):
            return types.SimpleNamespace(uuid=characteristic, handle=1)

    class Backend:
        def __init__(self, ble_device, *, disconnected_callback, **kwargs):
            assert ble_device is device
            self.disconnected_callback = disconnected_callback
            self.is_connected = False
            self.services = Services()
            self.mtu_size = 23
            self.notify_callback = None
            self.disconnect_count = 0
            self.block_connect = False
            self.connect_started = asyncio.Event()
            self.connect_cancelled = asyncio.Event()
            transports.append(self)

        async def connect(self, pair=False, **kwargs):
            self.connect_started.set()
            if self.block_connect:
                try:
                    await asyncio.Event().wait()
                finally:
                    # Proxy/backend cancellation owns its unfinished link.
                    self.connect_cancelled.set()
                    self.is_connected = False
            self.is_connected = True

        async def disconnect(self):
            self.disconnect_count += 1
            self.is_connected = False
            self.disconnected_callback()

        async def start_notify(self, characteristic, callback, **kwargs):
            assert characteristic.uuid == module.NOTIFY_CHAR_UUID
            self.notify_callback = callback

    def select_backend(self, selected_manager):
        assert selected_manager is wrapper_manager
        return wrappers._HaWrappedBleakBackend(
            device, scanner, Backend, "test-proxy", "controlled-proxy",
        )

    monkeypatch.setattr(
        wrappers.HaBleakClientWrapper,
        "_async_get_best_available_backend_and_device", select_backend,
    )
    owner = module.RadiaCodeBLEClient()
    # Wire-level handshake/framing is covered by the dependency-free client suite.
    monkeypatch.setattr(owner, "_execute_locked", AsyncMock(return_value=struct.pack("<I", 1)))
    monkeypatch.setattr(owner, "_read_vs_locked", AsyncMock(return_value=b""))
    try:
        yield module, owner, device, transports, scanner, Backend
    finally:
        await owner.disconnect()
        manager.async_stop()
        habluetooth.set_manager(previous_manager)


@pytest.mark.asyncio
async def test_tracked_cache_client_survives_ha_constructor_interception(connection_runtime):
    module, owner, device, transports, scanner, _ = connection_runtime
    await owner.connect(device)
    assert issubclass(module.BleakClientWithServiceCache, wrappers.HaBleakClientWrapper)
    assert isinstance(owner._client, module.BleakClientWithServiceCache)
    assert owner._client in scanner._clients
    assert owner.is_connected
    assert len(transports) == 1
    assert transports[0].notify_callback is not None
    owner._execute_locked.assert_awaited()
    await owner.disconnect()
    assert transports[0].disconnect_count == 1
    assert not scanner._clients
    assert not owner.is_connected


@pytest.mark.asyncio
async def test_outer_deadline_cancels_real_connector_and_ha_wrapper(connection_runtime, monkeypatch):
    module, owner, device, transports, scanner, backend_type = connection_runtime
    original_constructor = backend_type.__init__

    def stalled_constructor(self, *args, **kwargs):
        original_constructor(self, *args, **kwargs)
        self.block_connect = True

    monkeypatch.setattr(backend_type, "__init__", stalled_constructor)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(owner.connect(device), 0.4)
    assert len(transports) == 1
    assert transports[0].connect_started.is_set()
    assert transports[0].connect_cancelled.is_set()
    assert not owner.is_connected
    assert owner._client is None
    assert not owner._cmd_lock.locked()
    assert not scanner._clients
    owner._execute_locked.assert_not_awaited()
    steps = owner.transport_diagnostics["initialization_steps"]
    assert steps[0]["step"] == "establish_connection"
    assert steps[0]["outcome"] == "TimeoutError"
