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
