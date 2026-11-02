"""Storage cannot authorize an unverified device or firmware's schema."""
import asyncio

import pytest

from test_client import client_module, FakeBLETransport, supply_connection, _sensor_configuration_handler
from test_scalar_schema import CONFIG, frames
from test_coordinator import coordinator_module, make_coordinator, clock

SERIAL = "RC-103-000123"
ADDRESS = "AA:BB:CC:DD:EE:FF"


def verified_client(module):
    client = module.RadiaCodeBLEClient()
    client._configuration_address = ADDRESS
    client._firmware_version = "4.15"
    client._firmware_check_pending = False
    return client


def snapshot(module):
    client = verified_client(module)
    client._cache_record_configuration(CONFIG)
    return client.configuration_snapshot(SERIAL)


def test_snapshot_requires_complete_supported_schema_and_verified_firmware(client_module):
    client = verified_client(client_module)
    assert client.configuration_snapshot(SERIAL) is None
    client._cache_record_configuration(CONFIG)
    assert client.configuration_snapshot(SERIAL) is not None
    client._firmware_check_pending = True
    assert client.configuration_snapshot(SERIAL) is None
    client._firmware_check_pending = False
    for invalid in ("[DataStructure]", CONFIG[:-40], CONFIG + CONFIG):
        client._cache_record_configuration(invalid)
        assert client.configuration_snapshot(SERIAL) is None
    client._cache_record_configuration(CONFIG)
    assert client.configuration_snapshot("RC-ZERO-000123") is None


@pytest.mark.parametrize("field,value", [
    ("serial", "RC-103-999999"), ("address", "11:22:33:44:55:66"),
    ("firmware", "4.14"), ("parser_version", 1), ("parser_version", 3), ("firmware_build", "wrong"), ("sha256", "wrong"),
    ("configuration", CONFIG + "corrupt"), ("configuration", None),
    ("configuration", "\ud800"), ("parser_version", True),
])
def test_mismatched_cache_never_applies(client_module, field, value):
    saved = snapshot(client_module)
    saved[field] = value
    client = verified_client(client_module)
    assert not client.restore_configuration_snapshot(saved, SERIAL)
    assert not client._configuration_loaded
    assert client.configuration_retry_due


def test_provisional_cache_cannot_apply_before_firmware_confirmation(client_module):
    client = verified_client(client_module)
    client._firmware_check_pending = True
    assert not client.restore_configuration_snapshot(snapshot(client_module), SERIAL)
    assert not client._configuration_loaded


def test_cache_requires_a_known_firmware_even_if_pending_flag_is_clear(client_module):
    client = verified_client(client_module)
    saved = snapshot(client_module)
    client._firmware_version = saved["firmware"] = None
    assert not client.restore_configuration_snapshot(saved, SERIAL)


def test_cache_survives_reload_and_reconnect_without_configuration_transfer(client_module, monkeypatch):
    async def scenario():
        saved = snapshot(client_module)
        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(client_module, _sensor_configuration_handler(client_module, CONFIG, frames()))
        device = supply_connection(monkeypatch, client_module, transport)
        device.address = ADDRESS
        await client.connect(device)
        assert not client.restore_configuration_snapshot(saved, SERIAL)
        client._firmware_version = "4.15"
        client._firmware_check_pending = False
        assert client.restore_configuration_snapshot(saved, SERIAL)
        assert client.transport_diagnostics["configuration"]["source"] == "storage"
        assert not client.configuration_retry_due
        assert (await client.get_data()).battery == 87.5
        await client.disconnect()
        new_transport = FakeBLETransport(client_module, _sensor_configuration_handler(client_module, CONFIG, frames()))
        supply_connection(monkeypatch, client_module, new_transport)
        await client.connect(device)
        # A cached layout is withheld during firmware verification.
        assert (await client.get_data()).count_rate is None
        client._firmware_check_pending = False
        assert (await client.get_data()).battery == 87.5
        await client.disconnect()
    asyncio.run(scenario())


def test_storage_failures_do_not_retire_ble_or_erase_live_metadata(make_coordinator):
    async def scenario():
        c = make_coordinator()
        c._configuration_store.async_load.side_effect = OSError("disk")
        await c._async_restore_configuration()
        assert c._configuration_storage_error
        assert c._client.is_connected
        c._client.configuration_snapshot.return_value = {"test": "snapshot"}
        c._configuration_store.async_save.side_effect = OSError("disk")
        await c._async_save_configuration()
        assert c._stored_configuration is None
        assert c._client.is_connected
        c._configuration_store.async_save.side_effect = None
        await c._async_save_configuration()
        assert c._stored_configuration == {"test": "snapshot"}
        assert not c._configuration_storage_error
        await c.async_shutdown()
    asyncio.run(scenario())


def test_storage_loads_once_and_removes_rejected_metadata(make_coordinator):
    async def scenario():
        c = make_coordinator()
        c._configuration_store.async_load.return_value = {"invalid": True}
        await c._async_restore_configuration()
        await c._async_restore_configuration()
        c._configuration_store.async_load.assert_awaited_once()
        c._configuration_store.async_remove.assert_awaited_once()
        assert c._stored_configuration is None
        await c.async_shutdown()
    asyncio.run(scenario())
