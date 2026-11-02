"""Private HA Store round trips and verified client schema activation."""
import struct
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_asyncio")

from custom_components.radiacode.coordinator import RadiaCodeCoordinator
from custom_components.radiacode.radiacode_ble.client import RadiaCodeBLEClient
from test_coordinator_runtime import runtime


def application_version(build):
    boot = b"Feb  6 2023 15:49:14"
    target = build.encode("ascii") + b"\0"
    return struct.pack("<HHB", 0, 4, len(boot)) + boot + struct.pack("<HHB", 15, 4, len(target)) + target

CONFIG = (Path(__file__).parents[1] / "fixtures/scalar-1xx.txt").read_text()


def identity_client(address, firmware_minor=15):
    client = RadiaCodeBLEClient()
    client._configuration_address = address
    client._connection_generation = 1
    client._firmware_check_pending = True
    client.get_serial_number = AsyncMock(return_value="RC-103-000123")
    client._execute = AsyncMock(return_value=struct.pack("<HHBHHB", 0, 4, 0, firmware_minor, 4, 0))
    return client


@pytest.mark.asyncio
async def test_real_store_reload_and_confirmed_firmware_invalidation(runtime):
    hass, original, _, _, _ = runtime
    first = RadiaCodeCoordinator(hass, original.config_entry)
    second = RadiaCodeCoordinator(hass, original.config_entry)
    first._client = identity_client(first.address)
    second._client = identity_client(second.address)
    try:
        await first._fetch_device_identity()
        first._client._cache_record_configuration(CONFIG)
        await first._async_save_configuration()
        assert first._stored_configuration is not None
        stored = await second._configuration_store.async_load()
        assert stored["configuration"] == CONFIG
        assert not second._client._configuration_loaded
        await second._fetch_device_identity()
        assert second._client._configuration_loaded
        assert not second._client.configuration_retry_due
        assert second.transport_status["configuration"]["source"] == "storage"
        assert "configuration" not in second.transport_status["configuration_storage"]
        # The actual GET_VERSION parser invalidates both live metadata and disk.
        second._client._execute.return_value = struct.pack("<HHBHHB", 0, 4, 0, 16, 4, 0)
        await second._fetch_device_identity()
        assert not second._client._configuration_loaded
        assert second._client.configuration_retry_due
        assert await second._configuration_store.async_load() is None
    finally:
        await first.async_shutdown()
        await second.async_shutdown()


@pytest.mark.asyncio
async def test_store_identity_mismatch_keeps_discovery_available(runtime):
    hass, original, _, _, _ = runtime
    c = RadiaCodeCoordinator(hass, original.config_entry)
    c._client = identity_client(c.address)
    try:
        await c._fetch_device_identity()
        c._client._cache_record_configuration(CONFIG)
        snapshot = c._client.configuration_snapshot("RC-103-999999")
        await c._configuration_store.async_save(snapshot)
        c._client = identity_client(c.address)
        c._configuration_store_loaded = False
        await c._fetch_device_identity()
        assert not c._client._configuration_loaded
        assert c._client.configuration_retry_due
        assert await c._configuration_store.async_load() is None
    finally:
        await c.async_shutdown()


@pytest.mark.asyncio
async def test_known_build_bootstrap_and_same_version_build_invalidation(runtime):
    hass, original, _, _, _ = runtime
    c = RadiaCodeCoordinator(hass, original.config_entry)
    client = RadiaCodeBLEClient()
    client._configuration_address = c.address
    client._connection_generation = 1
    client._firmware_check_pending = True
    client._read_vs = AsyncMock(return_value=b"RC-103-000123\0")
    client._execute = AsyncMock(return_value=application_version("Oct 15 2026 10:18:10"))
    c._client = client
    try:
        await c._fetch_device_identity()
        assert c._fw_version == "4.15"
        assert c.transport_status["configuration"]["source"] == "firmware_profile"
        assert not client.configuration_retry_due
        # No bundled declaration is saved under the guise of a device download.
        await c._async_save_configuration()
        assert await c._configuration_store.async_load() is None
        client._read_vs.assert_awaited_once()
        assert len(client._execute.await_args_list) == 1
        # A later complete device read can be saved with this exact target build.
        actual = (Path(__file__).parents[1] / "fixtures/firmware/rc103-4.15-apple.txt").read_text()
        client._cache_record_configuration(actual)
        await c._async_save_configuration()
        assert (await c._configuration_store.async_load())["firmware_build"] == "Oct 15 2026 10:18:10"
        # Same numeric version, new build: invalidate memory and the real HA Store.
        c._sfr_attempted = True
        client._execute.return_value = application_version("Oct 15 2026 10:18:11")
        await c._fetch_device_identity()
        assert not client._configuration_loaded and client.configuration_retry_due
        assert not c._sfr_attempted
        assert await c._configuration_store.async_load() is None
    finally:
        await c.async_shutdown()


@pytest.mark.asyncio
async def test_entry_setup_unload_and_reload_reactivate_profile(runtime, monkeypatch):
    """Real entry/coordinator lifecycle, controlled identity replies, no radio."""
    from custom_components import radiacode as integration
    from custom_components.radiacode.diagnostics import async_get_config_entry_diagnostics

    hass, original, _, _, _ = runtime
    entry = original.config_entry
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True))
    created = []

    def coordinator_factory(hass, entry):
        coordinator = RadiaCodeCoordinator(hass, entry)
        client = coordinator._client
        client._configuration_address = coordinator.address
        client._connection_generation = 1
        client._firmware_check_pending = True
        client._read_vs = AsyncMock(return_value=b"RC-103-000123\0")
        client._execute = AsyncMock(return_value=application_version("Oct 15 2026 10:18:10"))
        created.append(coordinator)
        return coordinator

    monkeypatch.setattr(integration, "RadiaCodeCoordinator", coordinator_factory)
    try:
        for _ in range(2):
            assert await integration.async_setup_entry(hass, entry)
            coordinator = hass.data["radiacode"][entry.entry_id]
            assert not coordinator._client._configuration_loaded
            assert await coordinator._configuration_store.async_load() is None
            await coordinator._fetch_device_identity()
            result = await async_get_config_entry_diagnostics(hass, entry)
            assert result["transport"]["configuration"]["source"] == "firmware_profile"
            assert result["transport"]["configuration"]["firmware_profile"]["name"] == "RC-103-4.15-Oct 15 2026 10:18:10"
            assert not coordinator._client.configuration_retry_due
            # Serial + version only; no configuration or flash/image command.
            coordinator._client._read_vs.assert_awaited_once()
            assert len(coordinator._client._execute.await_args_list) == 1
            await coordinator._async_save_configuration()
            assert await coordinator._configuration_store.async_load() is None
            assert await integration.async_unload_entry(hass, entry)
            assert coordinator._stopping
            assert entry.entry_id not in hass.data["radiacode"]
        assert created[0] is not created[1]
        assert created[0]._client is not created[1]._client
    finally:
        for coordinator in created:
            await coordinator.async_shutdown()
