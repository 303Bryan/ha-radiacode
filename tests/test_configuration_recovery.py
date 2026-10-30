"""Bound optional configuration transfers across real framed BLE reconnects."""
import asyncio
import struct
import types

import pytest

from test_client import (
    client_module, FakeBLETransport, supply_connection,
    _MODERN_RECORD_CONFIGURATION, _live_sensor_record,
    _sensor_configuration_handler, _virtual_string_requests,
)


def partial_configuration_handler(module):
    normal = _sensor_configuration_handler(
        module, _MODERN_RECORD_CONFIGURATION, _live_sensor_record(),
    )

    def handler(transport, request):
        command = struct.unpack_from("<H", request, 4)[0]
        if command == module.CMD.GET_VERSION:
            transport.reply(request, struct.pack("<HHBHHB", 0, 4, 0, 15, 4, 0))
        elif command == module.CMD.RD_VIRT_STRING and struct.unpack_from("<I", request, 8)[0] == module.VS.CONFIGURATION:
            transport.notify(struct.pack("<i", 80) + request[4:8] + b"partial")
        else:
            normal(transport, request)

    return handler


async def exhaust_budget(module, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(module, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    client = module.RadiaCodeBLEClient()
    transports = []
    for _ in range(3):
        transport = FakeBLETransport(module, partial_configuration_handler(module))
        transports.append(transport)
        device = supply_connection(monkeypatch, module, transport)
        await client.connect(device)
        assert await client.get_firmware_version() == "4.15"
        assert (await client.get_data()).count_rate == pytest.approx(12.5)
        with pytest.raises(TimeoutError):
            await client.refresh_record_configuration()
        assert not client.is_connected
        assert transport.disconnect_count == 1
        clock[0] += 301
    return client, clock, device, transports


def test_failed_configuration_stops_after_three_attempts_across_reconnects(client_module, monkeypatch):
    async def scenario():
        client, clock, device, transports = await exhaust_budget(client_module, monkeypatch)
        for _ in range(8):
            transport = FakeBLETransport(client_module, partial_configuration_handler(client_module))
            transports.append(transport)
            supply_connection(monkeypatch, client_module, transport)
            await client.connect(device)
            assert await client.get_firmware_version() == "4.15"
            clock[0] += 301
            await client.refresh_record_configuration()
            assert (await client.get_data()).count_rate == pytest.approx(12.5)
            assert client.is_connected
            assert transport.disconnect_count == 0
            assert not client.configuration_retry_due
            diagnostics = client.transport_diagnostics["configuration"]
            assert diagnostics["automatic_paused"]
            assert diagnostics["automatic_attempts"] == 3
            assert diagnostics["automatic_attempt_limit"] == 3
            assert diagnostics["retry_in_seconds"] is None
            assert not diagnostics["loaded"]
            assert diagnostics["record_layout"] == []
            await client.disconnect()  # The BLE Connection switch cannot re-arm it.
        assert sum(_virtual_string_requests(client_module, t).count(client_module.VS.CONFIGURATION)
                   for t in transports) == 3
    asyncio.run(scenario())


@pytest.mark.parametrize("recovery", ["firmware", "address", "reload", "explicit_read"])
def test_configuration_can_recover_after_automatic_pause(client_module, monkeypatch, recovery):
    async def scenario():
        client, clock, device, _ = await exhaust_budget(client_module, monkeypatch)
        handler = _sensor_configuration_handler(client_module, _MODERN_RECORD_CONFIGURATION)

        def complete(transport, request):
            if struct.unpack_from("<H", request, 4)[0] == client_module.CMD.GET_VERSION:
                transport.reply(request, struct.pack("<HHBHHB", 0, 4, 0, 16, 4, 0))
            else:
                handler(transport, request)

        if recovery == "reload":
            client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(client_module, complete)
        supply_connection(monkeypatch, client_module, transport)
        if recovery == "address":
            device = types.SimpleNamespace(address="11:22:33:44:55:66")
        await client.connect(device)
        if recovery == "firmware":
            assert client.configuration_automatic_paused
            assert await client.get_firmware_version() == "4.16"
        if recovery == "explicit_read":
            assert client.configuration_automatic_paused
            assert await client.get_configuration() == _MODERN_RECORD_CONFIGURATION
        else:
            assert client.configuration_retry_due
            await client.refresh_record_configuration()
        diagnostics = client.transport_diagnostics["configuration"]
        assert diagnostics["loaded"]
        assert diagnostics["group4_schema"] == client_module.Group4Format.DOSE_COUNTER.value
        assert diagnostics["automatic_attempts"] == 0
        assert not diagnostics["automatic_paused"]
        assert not client.configuration_retry_due
        assert diagnostics["retry_in_seconds"] is None
        assert _virtual_string_requests(client_module, transport).count(client_module.VS.CONFIGURATION) == 1
        await client.disconnect()
    asyncio.run(scenario())


def test_cancelled_configuration_consumes_attempts_and_releases_transport(client_module, monkeypatch):
    async def scenario():
        clock = [100.0]
        monkeypatch.setattr(client_module, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
        client = client_module.RadiaCodeBLEClient()
        entered = asyncio.Event()

        async def blocked(transport, request):
            if struct.unpack_from("<H", request, 4)[0] == client_module.CMD.RD_VIRT_STRING and struct.unpack_from("<I", request, 8)[0] == client_module.VS.CONFIGURATION:
                entered.set()
                await asyncio.Event().wait()
            else:
                transport.reply_success(transport, request)

        for attempt in range(1, 4):
            entered.clear()
            transport = FakeBLETransport(client_module, blocked)
            device = supply_connection(monkeypatch, client_module, transport)
            await client.connect(device)
            task = asyncio.create_task(client.refresh_record_configuration())
            await asyncio.wait_for(entered.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not client._cmd_lock.locked()
            assert not client.is_connected
            assert transport.disconnect_count == 1
            assert client.transport_diagnostics["configuration"]["automatic_attempts"] == attempt
            clock[0] += 301
        assert client.configuration_automatic_paused
        assert not client.configuration_retry_due
        await client.refresh_record_configuration()
        assert client.transport_diagnostics["configuration"]["automatic_attempts"] == 3
    asyncio.run(scenario())
