"""BLE transport tests with real protocol frames and a dependency-free fake.

These cover framing, proxy/backend stalls and lifecycle races.  They do not
simulate detector firmware load or validate a physical Bluetooth radio.
"""

import asyncio
import datetime
import importlib.util
import logging
import struct
import sys
import types
from pathlib import Path

import pytest

_BLE_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components" / "radiacode" / "radiacode_ble"
)


@pytest.fixture
def client_module(monkeypatch):
    """Load the client without importing Home Assistant or real BLE stacks."""
    package_name = "_radiacode_client_tests"
    package = types.ModuleType(package_name)
    package.__path__ = [str(_BLE_DIR)]
    monkeypatch.setitem(sys.modules, package_name, package)

    bleak = types.ModuleType("bleak")
    bleak.BleakClient = object
    backends = types.ModuleType("bleak.backends")
    device = types.ModuleType("bleak.backends.device")
    device.BLEDevice = object
    connector = types.ModuleType("bleak_retry_connector")

    async def no_hardware(*args, **kwargs):
        raise AssertionError("Tests must supply a fake BLE transport")

    connector.BleakClientWithServiceCache = type("BleakClientWithServiceCache", (), {})
    connector.establish_connection = no_hardware
    for name, module in (
        ("bleak", bleak), ("bleak.backends", backends),
        ("bleak.backends.device", device), ("bleak_retry_connector", connector),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    loaded = {}
    for name in ("protocol", "client"):
        qualified = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(qualified, _BLE_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qualified, module)
        spec.loader.exec_module(module)
        loaded[name] = module
    module = loaded["client"]
    module._CMD_TIMEOUT = 0.08
    module._STALL_TIMEOUT = 0.015
    module._DISCONNECT_TIMEOUT = 0.02
    module._CONNECT_TIMEOUT = 0.08
    return module


class FakeServices(list):
    """Expose the service and write properties public Bleak APIs provide."""

    def __init__(self, module):
        self.characteristic = types.SimpleNamespace(
            uuid=module.WRITE_CHAR_UUID,
            properties=["write", "write-without-response"],
        )
        super().__init__([types.SimpleNamespace(uuid=module.SERVICE_UUID)])

    def get_characteristic(self, uuid):
        return self.characteristic if uuid == self.characteristic.uuid else None


class FakeBLETransport:
    """Record writes and deliver controlled responses from the same loop."""

    def __init__(self, module, handler=None):
        self.module = module
        self.handler = handler or self.reply_success
        self.is_connected = True
        self.services = FakeServices(module)
        self.notify_callback = None
        self.disconnected_callback = None
        self.disconnect_count = 0
        self.requests = []
        self.writes = []
        self.write_responses = []
        self.pending = bytearray()
        self.write_started = asyncio.Event()

    async def start_notify(self, characteristic, callback):
        assert characteristic == self.module.NOTIFY_CHAR_UUID
        self.notify_callback = callback

    async def write_gatt_char(self, characteristic, chunk, *, response):
        assert characteristic == self.module.WRITE_CHAR_UUID
        self.write_responses.append(response)
        assert len(chunk) <= 18
        self.writes.append(bytes(chunk))
        self.write_started.set()
        self.pending.extend(chunk)
        if len(self.pending) < 4:
            return
        size = struct.unpack_from("<I", self.pending)[0] + 4
        if len(self.pending) >= size:
            request = bytes(self.pending[:size])
            del self.pending[:size]
            self.requests.append(request)
            result = self.handler(self, request)
            if result is not None:
                await result

    def notify(self, data):
        if self.notify_callback is not None:
            self.notify_callback(object(), bytearray(data))

    def reply(self, request, payload=b"", *, packet_size=20):
        body = request[4:8] + payload
        frame = struct.pack("<i", len(body)) + body
        for offset in range(0, len(frame), packet_size):
            self.notify(frame[offset: offset + packet_size])

    def reply_success(self, transport, request):
        cmd = struct.unpack_from("<H", request, 4)[0]
        if cmd == self.module.CMD.SET_EXCHANGE:
            payload = struct.pack("<IHHI", 0xFF03FF33, 65535, 65535, 0x0201)
        elif cmd == self.module.CMD.RD_VIRT_STRING:
            payload = struct.pack("<II", 1, 0)
        else:
            payload = struct.pack("<I", 1)
        self.reply(request, payload)

    async def disconnect(self):
        self.disconnect_count += 1
        self.is_connected = False
        if self.disconnected_callback is not None:
            self.disconnected_callback(self)

    def drop_link(self):
        self.is_connected = False
        self.disconnected_callback(self)


def attached_client(module, handler=None, *, acknowledged_writes=False):
    client = module.RadiaCodeBLEClient(acknowledged_writes=acknowledged_writes)
    transport = FakeBLETransport(module, handler)
    client._client = transport
    transport.notify_callback = lambda sender, data: client._on_client_notify(
        transport, sender, data
    )
    transport.disconnected_callback = client._on_ble_disconnect
    return client, transport


def supply_connection(monkeypatch, module, transport):
    async def establish(_client_type, _device, _name, **kwargs):
        transport.disconnected_callback = kwargs["disconnected_callback"]
        return transport

    monkeypatch.setattr(module, "establish_connection", establish)
    return types.SimpleNamespace(address="AA:BB:CC:DD:EE:FF")


def supply_tracked_client_type(monkeypatch, module, transports):
    """Model the connector constructing its transport before awaiting connect."""
    class CachedTransport(FakeBLETransport):
        def __init__(self, device, *, disconnected_callback, **kwargs):
            super().__init__(module)
            self.device = device
            self.disconnected_callback = disconnected_callback
            self.is_connected = False
            transports.append(self)

        async def clear_cache(self):
            return True

    monkeypatch.setattr(module, "BleakClientWithServiceCache", CachedTransport)
    return CachedTransport


def test_establishment_uses_cache_client_without_ineffective_timeout_kwarg(
    client_module, monkeypatch,
):
    async def scenario():
        transports = []
        cached_type = supply_tracked_client_type(monkeypatch, client_module, transports)
        device = types.SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
        client = client_module.RadiaCodeBLEClient()

        async def establish(client_type, supplied_device, name, **kwargs):
            assert issubclass(client_type, cached_type)
            assert supplied_device is device
            assert name == device.address
            assert kwargs["max_attempts"] == 2
            assert "timeout" not in kwargs
            transport = client_type(supplied_device, **kwargs)
            # It must be owned before the asynchronous connection starts.
            assert client._client is transport
            transport.is_connected = True
            return transport

        monkeypatch.setattr(client_module, "establish_connection", establish)
        await client.connect(device)
        assert len(transports) == 1
        assert len(transports[0].requests) == 5
        assert client.is_connected
        await client.disconnect()
        assert transports[0].disconnect_count == 1

    asyncio.run(scenario())



def test_internal_connection_retry_clears_failed_attempt_disconnect_event(
    client_module, monkeypatch,
):
    async def scenario():
        transports = []
        supply_tracked_client_type(monkeypatch, client_module, transports)
        client = client_module.RadiaCodeBLEClient()
        device = types.SimpleNamespace(address="AA:BB:CC:DD:EE:FF")

        async def establish(client_type, supplied_device, name, **kwargs):
            transport = client_type(supplied_device, **kwargs)
            transport.is_connected = True
            transport.drop_link()
            assert client._disconnected_event.is_set()
            assert client._notify_event.is_set()
            # The connector retries with the same client rather than constructing
            # another one. Return its later successful attempt.
            await asyncio.sleep(0)
            transport.is_connected = True
            return transport

        monkeypatch.setattr(client_module, "establish_connection", establish)
        await client.connect(device)
        assert len(transports) == 1
        assert len(transports[0].requests) == 5
        assert transports[0].disconnect_count == 0
        assert not client._disconnected_event.is_set()
        assert client.is_connected
        await client.disconnect()

    asyncio.run(scenario())

def test_stuck_establishment_deadline_cleans_partially_created_transport(
    client_module, monkeypatch,
):
    async def scenario():
        transports = []
        supply_tracked_client_type(monkeypatch, client_module, transports)
        cancelled = asyncio.Event()
        client = client_module.RadiaCodeBLEClient()
        device = types.SimpleNamespace(address="AA:BB:CC:DD:EE:FF")

        async def establish(client_type, supplied_device, name, **kwargs):
            client_type(supplied_device, **kwargs)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(client_module, "establish_connection", establish)
        started = asyncio.get_running_loop().time()
        with pytest.raises(asyncio.TimeoutError):
            await client.connect(device)
        elapsed = asyncio.get_running_loop().time() - started
        assert client_module._CONNECT_TIMEOUT <= elapsed < 0.3
        assert cancelled.is_set()
        assert transports[0].disconnect_count == 1
        assert transports[0].notify_callback is None
        assert not transports[0].requests
        assert not client.is_connected
        assert not client._cmd_lock.locked()
        steps = client.transport_diagnostics["initialization_steps"]
        assert steps[0]["step"] == "establish_connection"
        assert steps[0]["outcome"] == "TimeoutError"

    asyncio.run(scenario())


def test_cancelled_establishment_finishes_cleanup_before_queued_reconnect(
    client_module, monkeypatch,
):
    async def scenario():
        client_module._DISCONNECT_TIMEOUT = 0.3
        transports = []
        supply_tracked_client_type(monkeypatch, client_module, transports)
        started = asyncio.Event()
        establishment_cancelled = asyncio.Event()
        cleanup_started = asyncio.Event()
        finish_cleanup = asyncio.Event()
        cleanup_cancelled = asyncio.Event()
        client = client_module.RadiaCodeBLEClient()
        device = types.SimpleNamespace(address="AA:BB:CC:DD:EE:FF")

        async def establish(client_type, supplied_device, name, **kwargs):
            transport = client_type(supplied_device, **kwargs)
            if len(transports) > 1:
                transport.is_connected = True
                return transport

            async def slow_disconnect():
                transport.disconnect_count += 1
                cleanup_started.set()
                try:
                    await finish_cleanup.wait()
                    transport.is_connected = False
                except asyncio.CancelledError:
                    cleanup_cancelled.set()
                    raise

            transport.disconnect = slow_disconnect
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                establishment_cancelled.set()

        monkeypatch.setattr(client_module, "establish_connection", establish)
        connecting = asyncio.create_task(client.connect(device))
        await started.wait()
        connecting.cancel()
        await cleanup_started.wait()
        connecting.cancel()
        await asyncio.sleep(0)
        connecting.cancel()
        reconnect = asyncio.create_task(client.connect(device))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert establishment_cancelled.is_set()
        assert not cleanup_cancelled.is_set()
        assert not connecting.done()
        assert len(transports) == 1
        assert client._cmd_lock.locked()
        finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await connecting
        await reconnect
        assert transports[0].disconnect_count == 1
        assert len(transports[1].requests) == 5
        assert client.is_connected
        assert not client._cmd_lock.locked()
        await client.disconnect()

    asyncio.run(scenario())


def test_complete_fragmented_reply_and_chunked_write(client_module):
    async def scenario():
        payload = bytes(range(256)) * 2
        client, transport = attached_client(
            client_module, lambda transport, request: transport.reply(request, payload)
        )
        actual = await client._execute(client_module.CMD.SET_EXCHANGE, b"x" * 45)
        assert actual == payload
        assert len(transport.writes) == 3
        assert client.is_connected
        assert not client._expecting_response
        assert client._resp_total == 0

    asyncio.run(scenario())


def progressive_reply_handler(payload, tasks, delay=0.01):
    """Deliver fragments independently of completion of the backend write."""
    def handler(transport, request):
        async def deliver():
            body = request[4:8] + payload
            frame = struct.pack("<i", len(body)) + body
            for offset in range(0, len(frame), 20):
                if not transport.is_connected:
                    return
                transport.notify(frame[offset:offset + 20])
                await asyncio.sleep(delay)
        tasks.append(asyncio.create_task(deliver()))
    return handler


@pytest.mark.parametrize("target", ["SPECTRUM", "SPEC_ACCUM", "CONFIGURATION", "SFR_FILE"])
def test_progressing_bulk_reply_outlives_ordinary_deadline(client_module, target):
    async def scenario():
        client_module._CMD_TIMEOUT = 0.04
        client_module._BULK_CMD_TIMEOUT = 0.3
        client_module._STALL_TIMEOUT = 0.04
        raw = b"x" * 200
        tasks = []
        client, transport = attached_client(
            client_module,
            progressive_reply_handler(struct.pack("<II", 1, len(raw)) + raw, tasks),
        )
        assert await client._read_vs(getattr(client_module.VS, target)) == raw
        await asyncio.gather(*tasks)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["elapsed_s"] > client_module._CMD_TIMEOUT
        assert stats["outcome"] == "success"
        assert client.is_connected
        assert transport.disconnect_count == 0
    asyncio.run(scenario())


def test_bulk_without_first_reply_keeps_ordinary_deadline(client_module):
    async def scenario():
        client_module._BULK_CMD_TIMEOUT = 0.3
        client, transport = attached_client(client_module, lambda *_: None)
        with pytest.raises(TimeoutError, match="Timed out during RadiaCode command"):
            await asyncio.wait_for(client._read_vs(client_module.VS.SPECTRUM), 0.2)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["error_category"] == "no_response"
        assert stats["elapsed_s"] < 0.2
        assert transport.disconnect_count == 1
    asyncio.run(scenario())


def test_bulk_blocked_write_keeps_ordinary_deadline(client_module):
    async def scenario():
        client_module._BULK_CMD_TIMEOUT = 0.3
        async def blocked_write(*_):
            await asyncio.Event().wait()
        client, transport = attached_client(client_module, blocked_write)
        with pytest.raises(TimeoutError, match="Timed out during RadiaCode command"):
            await asyncio.wait_for(client._read_vs(client_module.VS.SPECTRUM), 0.2)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["error_category"] == "write_timeout"
        assert transport.disconnect_count == 1
    asyncio.run(scenario())


def test_bulk_incomplete_reply_keeps_interpacket_stall_deadline(client_module):
    async def scenario():
        client_module._BULK_CMD_TIMEOUT = 0.3
        def partial(transport, request):
            transport.notify(struct.pack("<i", 80) + request[4:8] + b"prefix")
        client, transport = attached_client(client_module, partial)
        with pytest.raises(TimeoutError, match="Incomplete response"):
            await client._read_vs(client_module.VS.SPECTRUM)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["error_category"] == "incomplete_response"
        assert stats["elapsed_s"] < client_module._CMD_TIMEOUT
        assert transport.disconnect_count == 1
    asyncio.run(scenario())


def test_progressing_bulk_reply_still_has_hard_deadline(client_module):
    async def scenario():
        client_module._CMD_TIMEOUT = 0.04
        client_module._BULK_CMD_TIMEOUT = 0.08
        client_module._STALL_TIMEOUT = 0.04
        raw = b"x" * 2000
        tasks = []
        client, transport = attached_client(
            client_module,
            progressive_reply_handler(struct.pack("<II", 1, len(raw)) + raw, tasks),
        )
        with pytest.raises(TimeoutError, match="Timed out during RadiaCode command"):
            await client._read_vs(client_module.VS.SPECTRUM)
        await asyncio.gather(*tasks)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["elapsed_s"] >= client_module._BULK_CMD_TIMEOUT
        assert stats["elapsed_s"] < 0.2
        assert stats["missing_body_bytes"] > 0
        assert transport.disconnect_count == 1
        assert not client.is_connected
    asyncio.run(scenario())


def test_primary_data_reply_keeps_ordinary_deadline_despite_progress(client_module):
    async def scenario():
        client_module._CMD_TIMEOUT = 0.04
        client_module._BULK_CMD_TIMEOUT = 0.3
        client_module._STALL_TIMEOUT = 0.04
        raw = b"x" * 200
        tasks = []
        client, transport = attached_client(
            client_module,
            progressive_reply_handler(struct.pack("<II", 1, len(raw)) + raw, tasks),
        )
        with pytest.raises(TimeoutError, match="Timed out during RadiaCode command"):
            await client._read_vs(client_module.VS.DATA_BUF)
        await asyncio.gather(*tasks)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["elapsed_s"] < 0.2
        assert stats["error_category"] == "incomplete_response"
        assert transport.disconnect_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_before_cleanup", [False, True])
def test_repeated_cancellation_finishes_cleanup_before_queued_reconnect(
    client_module, monkeypatch, cancel_before_cleanup,
):
    async def scenario():
        client_module._DISCONNECT_TIMEOUT = 0.3
        client, old = attached_client(client_module, lambda *_: None)
        cleanup_started = asyncio.Event()
        finish_cleanup = asyncio.Event()
        cleanup_cancelled = asyncio.Event()
        async def slow_disconnect():
            old.disconnect_count += 1
            cleanup_started.set()
            try:
                await finish_cleanup.wait()
                old.is_connected = False
            except asyncio.CancelledError:
                cleanup_cancelled.set()
                raise
        old.disconnect = slow_disconnect
        command = asyncio.create_task(client._execute(client_module.CMD.GET_VERSION))
        await old.write_started.wait()
        if cancel_before_cleanup:
            command.cancel()
        await cleanup_started.wait()  # cancellation or ordinary response timeout
        command.cancel()
        await asyncio.sleep(0)
        command.cancel()
        fresh = FakeBLETransport(client_module)
        device = supply_connection(monkeypatch, client_module, fresh)
        reconnect = asyncio.create_task(client.connect(device))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not cleanup_cancelled.is_set()
        assert not command.done()
        assert client._cmd_lock.locked()
        assert not fresh.requests
        finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await command
        await reconnect
        assert old.disconnect_count == 1
        assert not old.is_connected
        assert len(fresh.requests) == 5
        assert client.is_connected
        assert not client._cmd_lock.locked()
    asyncio.run(scenario())


def test_stalled_partial_frame_is_rejected_and_transport_retired(client_module):
    async def scenario():
        def partial(transport, request):
            transport.notify(struct.pack("<i", 80) + request[4:8] + b"prefix")

        client, transport = attached_client(client_module, partial)
        with pytest.raises(TimeoutError, match="Incomplete response"):
            await client._execute(client_module.CMD.RD_VIRT_STRING)
        assert transport.disconnect_count == 1
        assert not client.is_connected
        assert not client._expecting_response
        previous = bytes(client._resp_buf)
        transport.notify(b"late unframed continuation")
        assert bytes(client._resp_buf) == previous
        with pytest.raises(ConnectionError, match="Not connected"):
            await client._execute(client_module.CMD.GET_VERSION)
        assert len(transport.requests) == 1

    asyncio.run(scenario())


def test_dropped_middle_notification_with_final_tail_is_rejected(client_module):
    async def scenario():
        payload = bytes(range(256)) * 2
        expected_body = 4 + len(payload)

        async def missing_middle(transport, request):
            frame = struct.pack("<i", expected_body) + request[4:8] + payload
            for index, offset in enumerate(range(0, len(frame), 20)):
                if index == 8:
                    continue
                if index == 9:
                    await asyncio.sleep(0.003)
                transport.notify(frame[offset:offset + 20])

        client, transport = attached_client(client_module, missing_middle)
        with pytest.raises(TimeoutError, match="missing 20"):
            await client._read_vs(client_module.VS.CONFIGURATION)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["virtual_string"] == "CONFIGURATION"
        assert stats["declared_body_bytes"] == expected_body
        assert stats["received_body_bytes"] == expected_body - 20
        assert stats["missing_body_bytes"] == 20
        assert stats["notification_count"] == 25
        assert stats["notification_sizes"] == {"20": 25}
        assert stats["first_byte_s"] is not None
        assert stats["max_notification_gap_s"] >= 0.003
        assert stats["error_category"] == "incomplete_response"
        assert client.transport_diagnostics["last_disconnect_reason"] == "command:incomplete_response"
        assert transport.disconnect_count == 1

    asyncio.run(scenario())


def test_disconnected_partial_frame_cannot_be_success(client_module):
    async def scenario():
        def partial_then_drop(transport, request):
            transport.notify(struct.pack("<i", 80) + request[4:8] + b"prefix")
            transport.drop_link()

        client, transport = attached_client(client_module, partial_then_drop)
        with pytest.raises(ConnectionError, match="connection lost"):
            await client._execute(client_module.CMD.RD_VIRT_STRING)
        assert transport.disconnect_count == 1
        assert not client.is_connected

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad_frame,match",
    [
        (struct.pack("<i", -1), "Invalid BLE response body length"),
        (struct.pack("<i", 0), "Invalid BLE response body length"),
        (struct.pack("<i", 3) + b"abc", "Invalid BLE response body length"),
        (struct.pack("<i", 4) + b"overflow", "overflow"),
        (struct.pack("<i", 4) + b"\x00\x00\x00\x80", "echo header mismatch"),
    ],
)
def test_bad_frames_retire_transport(client_module, bad_frame, match):
    async def scenario():
        client, transport = attached_client(
            client_module, lambda transport, request: transport.notify(bad_frame)
        )
        with pytest.raises(ValueError, match=match):
            await client._execute(client_module.CMD.GET_VERSION)
        assert transport.disconnect_count == 1
        assert not client.is_connected
        assert not client._expecting_response

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["SERIAL_NUMBER", "CONFIGURATION", "SFR_FILE"])
def test_failure_previews_suppress_identity_and_configuration(client_module, caplog, target):
    async def scenario():
        secret = b"private-device-identifier"

        def invalid_echo(transport, request):
            body = b"\x00\x00\x00\x80" + secret
            transport.notify(struct.pack("<i", len(body)) + body)

        caplog.set_level(logging.DEBUG, logger=client_module.__name__)
        client, _ = attached_client(client_module, invalid_echo)
        with pytest.raises(ValueError, match="echo header mismatch"):
            await client._read_vs(getattr(client_module.VS, target))
        assert "body_prefix=<suppressed>" in caplog.text
        assert secret.hex() not in caplog.text
        assert secret.decode() not in caplog.text

    asyncio.run(scenario())


def test_numeric_failure_preview_is_bounded(client_module, caplog):
    async def scenario():
        body = b"\x00\x00\x00\x80" + bytes(range(100))
        caplog.set_level(logging.DEBUG, logger=client_module.__name__)
        client, _ = attached_client(client_module, lambda transport, request: transport.notify(struct.pack("<i", len(body)) + body))
        with pytest.raises(ValueError):
            await client._read_vs(client_module.VS.DATA_BUF)
        assert f"body_prefix={body[:32].hex()} " in caplog.text
        assert body[32:40].hex() not in caplog.text

    asyncio.run(scenario())


def test_no_response_has_hard_deadline(client_module):
    async def scenario():
        client, transport = attached_client(client_module, lambda *_: None)
        started = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError):
            await client._execute(client_module.CMD.GET_VERSION)
        assert asyncio.get_running_loop().time() - started < 0.3
        assert transport.disconnect_count == 1
        assert not client._expecting_response

    asyncio.run(scenario())


def test_hard_deadline_includes_a_stalled_backend_write(client_module):
    async def scenario():
        write_cancelled = asyncio.Event()

        async def blocked(transport, request):
            try:
                await asyncio.Event().wait()
            finally:
                write_cancelled.set()

        client, transport = attached_client(client_module, blocked)
        with pytest.raises(TimeoutError):
            await client._execute(client_module.CMD.GET_VERSION)
        assert write_cancelled.is_set()
        assert transport.disconnect_count == 1
        assert not client._expecting_response

    asyncio.run(scenario())


def test_backend_write_error_retires_transport(client_module):
    async def scenario():
        def broken(*_):
            raise RuntimeError("proxy write failed")

        client, transport = attached_client(client_module, broken)
        with pytest.raises(RuntimeError, match="proxy write failed"):
            await client._execute(client_module.CMD.GET_VERSION)
        assert transport.disconnect_count == 1
        assert not client._expecting_response

    asyncio.run(scenario())


def test_user_disconnect_wakes_pending_command(client_module):
    async def scenario():
        client, transport = attached_client(client_module, lambda *_: None)
        task = asyncio.create_task(client._execute(client_module.CMD.GET_VERSION))
        await transport.write_started.wait()
        await client.disconnect()
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(task, 0.02)
        assert transport.disconnect_count == 1
        assert not client._expecting_response

    asyncio.run(scenario())


@pytest.mark.parametrize("while_writing", [False, True])
def test_cancellation_releases_transport_and_command_lock(client_module, while_writing):
    async def scenario():
        async def block_write(*_):
            await asyncio.Event().wait()

        handler = block_write if while_writing else lambda *_: None
        client, transport = attached_client(client_module, handler)
        task = asyncio.create_task(client._execute(client_module.CMD.GET_VERSION))
        await transport.write_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.disconnect_count == 1
        assert not client.is_connected
        assert not client._expecting_response
        assert not client._cmd_lock.locked()

    asyncio.run(scenario())


def test_reconnect_filters_old_callbacks_during_new_command(client_module, monkeypatch):
    async def scenario():
        client, old = attached_client(client_module, lambda *_: None)
        task = asyncio.create_task(client._execute(client_module.CMD.GET_VERSION))
        await old.write_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        fresh = FakeBLETransport(client_module)
        device = supply_connection(monkeypatch, client_module, fresh)
        await client.connect(device)
        assert client.is_connected
        assert len(fresh.requests) == 5  # handshake, time, device time, configuration, drain

        def ghost_then_reply(transport, request):
            old.notify(b"late unframed continuation from old connection")
            old.disconnected_callback(old)
            assert not client._disconnected_event.is_set()
            assert not client._response_started
            transport.reply(request, b"fresh payload")

        fresh.handler = ghost_then_reply
        assert await client._execute(client_module.CMD.GET_VERSION) == b"fresh payload"
        assert client.is_connected
        assert fresh.disconnect_count == 0

    asyncio.run(scenario())


def test_failed_init_releases_device(client_module, monkeypatch):
    async def scenario():
        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(client_module)
        transport.services = []
        device = supply_connection(monkeypatch, client_module, transport)
        with pytest.raises(client_module.RadiaCodeInitError) as error:
            await client.connect(device)
        assert error.value.step == "service_discovery"
        assert transport.disconnect_count == 1
        assert not client.is_connected
        assert not client._cmd_lock.locked()

    asyncio.run(scenario())


def test_incomplete_init_drain_is_fatal(client_module, monkeypatch):
    async def scenario():
        def handler(transport, request):
            cmd = struct.unpack_from("<H", request, 4)[0]
            if (
                cmd == client_module.CMD.RD_VIRT_STRING
                and struct.unpack_from("<I", request, 8)[0] == client_module.VS.DATA_BUF
            ):
                transport.notify(struct.pack("<i", 80) + request[4:8])
            else:
                transport.reply_success(transport, request)

        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(client_module, handler)
        device = supply_connection(monkeypatch, client_module, transport)
        with pytest.raises(client_module.RadiaCodeInitError) as error:
            await client.connect(device)
        assert error.value.step == "data_buf"
        assert transport.disconnect_count == 1
        assert not client.is_connected

    asyncio.run(scenario())


def test_cancelled_init_releases_device(client_module, monkeypatch):
    async def scenario():
        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(client_module, lambda *_: None)
        device = supply_connection(monkeypatch, client_module, transport)
        task = asyncio.create_task(client.connect(device))
        await transport.write_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.disconnect_count == 1
        assert not client.is_connected
        assert not client._cmd_lock.locked()

    asyncio.run(scenario())


def test_repeated_commands_wrap_sequence_without_reconnecting(client_module):
    async def scenario():
        client, transport = attached_client(
            client_module, lambda transport, request: transport.reply(request, b"reading")
        )
        for _ in range(320):
            assert await client._execute(client_module.CMD.GET_VERSION) == b"reading"
        assert [request[7] for request in transport.requests] == [
            0x80 + (n % 32) for n in range(320)
        ]
        assert client.is_connected
        assert transport.disconnect_count == 0
        history = client.transport_diagnostics["recent_commands"]
        assert len(history) == client_module._COMMAND_HISTORY_LIMIT
        assert history[-1]["sequence"] == 319 % 32
        assert history[-1]["error_category"] is None
        assert "_started" not in history[-1]
        history[-1]["notification_sizes"]["20"] = -1
        assert -1 not in client.transport_diagnostics["recent_commands"][-1]["notification_sizes"].values()

    asyncio.run(scenario())


def test_concurrent_controls_and_poll_commands_are_serialized(client_module):
    async def scenario():
        async def delayed_reply(transport, request):
            await asyncio.sleep(0)
            transport.reply(request, request[8:])

        client, transport = attached_client(client_module, delayed_reply)
        results = await asyncio.gather(*[
            client._execute(client_module.CMD.GET_VERSION, bytes([n]) * 35)
            for n in range(12)
        ])
        assert results == [bytes([n]) * 35 for n in range(12)]
        assert len(transport.requests) == 12
        assert all(len(request) == 43 for request in transport.requests)
        assert not transport.pending
        assert client.is_connected

    asyncio.run(scenario())


def test_spectrum_reads_directly_and_keeps_observed_encoding_across_reconnect(client_module, monkeypatch):
    async def scenario():
        calls = []
        spectrum = struct.pack("<Ifff", 120, 0.0, 3.0, 0.0) + struct.pack("<1024I", *range(1024))

        def handler(transport, request):
            cmd = struct.unpack_from("<H", request, 4)[0]
            if cmd != client_module.CMD.RD_VIRT_STRING:
                transport.reply_success(transport, request)
                return
            vs_id = struct.unpack_from("<I", request, 8)[0]
            calls.append(vs_id)
            if vs_id in (client_module.VS.CONFIGURATION, client_module.VS.DATA_BUF):
                data = b""
            else:
                data = spectrum
            transport.reply(request, struct.pack("<II", 1, len(data)) + data)

        client, transport = attached_client(client_module, handler)
        assert client.spectrum_format_version is None
        assert client.spectrum_format_source is None
        assert len((await client.get_spectrum()).counts) == 1024
        assert client.spectrum_format_source == "complete_payload"
        with pytest.raises(AttributeError):
            client.spectrum_format_source = "configuration"
        assert len((await client.get_spectrum(accumulated=True)).counts) == 1024
        assert client.spectrum_format_version == 0
        assert calls == [
            client_module.VS.SPECTRUM,
            client_module.VS.SPEC_ACCUM,
        ]
        fresh = FakeBLETransport(client_module, handler)
        device = supply_connection(monkeypatch, client_module, fresh)
        await client.connect(device)
        assert transport.disconnect_count == 1
        assert client.spectrum_format_version == 0
        assert client._spectrum_format_loaded
        assert len((await client.get_spectrum()).counts) == 1024
        assert calls.count(client_module.VS.CONFIGURATION) == 1

    asyncio.run(scenario())


def test_full_frame_with_incomplete_spectrum_is_rejected(client_module):
    async def scenario():
        data = struct.pack("<Ifff", 120, 0.0, 3.0, 0.0) + b"\x00" * (512 * 4)

        def handler(transport, request):
            transport.reply(request, struct.pack("<II", 1, len(data)) + data)

        client, _ = attached_client(client_module, handler)
        client._spectrum_format_loaded = True
        client._spectrum_format_version = 0
        with pytest.raises(ValueError, match="incomplete .512 channels"):
            await client.get_spectrum()
        assert client.is_connected  # complete frame; invalid spectrum payload

    asyncio.run(scenario())


def test_each_spectrum_validates_encoding_even_after_a_format_is_learned(client_module):
    async def scenario():
        header = struct.pack("<Ifff", 120, 0.0, 3.0, 0.0)
        spectra = [header + struct.pack("<1024I", *range(1024)), header + b"\x00\x40"]

        def handler(transport, request):
            data = spectra.pop(0)
            transport.reply(request, struct.pack("<II", 1, len(data)) + data)

        client, transport = attached_client(client_module, handler)
        assert (await client.get_spectrum()).counts == list(range(1024))
        assert client.spectrum_format_version == 0
        assert (await client.get_spectrum()).counts == [0] * 1024
        assert client.spectrum_format_version == 1
        assert len(transport.requests) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("authoritative", [False, True])
def test_ambiguous_spectrum_requires_authoritative_configuration(client_module, authoritative):
    async def scenario():
        body = b"\xf5\x3f" + struct.pack("<1023I", *([100] * 1023)) + b"\x10\x00"
        data = struct.pack("<Ifff", 120, 0.0, 3.0, 0.0) + body

        def handler(transport, request):
            transport.reply(request, struct.pack("<II", 1, len(data)) + data)

        client, transport = attached_client(client_module, handler)
        client._spectrum_format_version = 1
        client._spectrum_format_loaded = True
        client._spectrum_format_authoritative = authoritative
        if authoritative:
            assert (await client.get_spectrum()).counts == [100] * 1023 + [0]
        else:
            with pytest.raises(ValueError, match="Ambiguous spectrum encoding"):
                await client.get_spectrum()
            assert client.transport_diagnostics["recent_commands"][-1]["error_category"] == "invalid_payload"
        assert len(transport.requests) == 1
        assert client.is_connected

    asyncio.run(scenario())


@pytest.mark.parametrize("configuration", [
    b"SpecFormatVersion=abc\n", b"SpecFormatVersion=\n", b"SpecFormatVersion=2\n",
    b"SpecFormatVersion=0\nSpecFormatVersion=1\n",
])
def test_malformed_configuration_cannot_authorize_ambiguous_spectrum(client_module, configuration):
    async def scenario():
        body = b"\xf5\x3f" + struct.pack("<1023I", *([100] * 1023)) + b"\x10\x00"
        spectrum = struct.pack("<Ifff", 120, 0.0, 3.0, 0.0) + body

        def handler(transport, request):
            target = struct.unpack_from("<I", request, 8)[0]
            data = configuration if target == client_module.VS.CONFIGURATION else spectrum
            transport.reply(request, struct.pack("<II", 1, len(data)) + data)

        client, _ = attached_client(client_module, handler)
        client._spectrum_format_version = 1
        client._spectrum_format_loaded = True  # previously uniquely validated
        with pytest.raises(ValueError, match="SpecFormatVersion declaration"):
            await client.refresh_spectrum_format()
        assert client.spectrum_format_version == 1
        assert not client._spectrum_format_authoritative
        with pytest.raises(ValueError, match="Ambiguous spectrum encoding"):
            await client.get_spectrum()

    asyncio.run(scenario())


@pytest.mark.parametrize("configuration,authoritative", [
    (b"Firmware=4.14\n", False), (b" SpecFormatVersion = 1\n", True),
])
def test_configuration_default_does_not_disambiguate_spectrum(client_module, configuration, authoritative):
    async def scenario():
        body = b"\xf5\x3f" + struct.pack("<1023I", *([100] * 1023)) + b"\x10\x00"
        spectrum = struct.pack("<Ifff", 120, 0.0, 3.0, 0.0) + body

        def handler(transport, request):
            target = struct.unpack_from("<I", request, 8)[0]
            data = configuration if target == client_module.VS.CONFIGURATION else spectrum
            transport.reply(request, struct.pack("<II", 1, len(data)) + data)

        client, _ = attached_client(client_module, handler)
        await client.refresh_spectrum_format()
        assert client._spectrum_format_authoritative is authoritative
        assert client.transport_diagnostics["spectrum_format_source"] == (
            "configuration" if authoritative else "configuration_default"
        )
        assert client.spectrum_format_source == (
            "configuration" if authoritative else "configuration_default"
        )
        if authoritative:
            assert (await client.get_spectrum()).counts == [100] * 1023 + [0]
        else:
            assert client.spectrum_format_version == 0
            with pytest.raises(ValueError, match="Ambiguous spectrum encoding"):
                await client.get_spectrum()

    asyncio.run(scenario())


@pytest.mark.parametrize("queued_operation", ["command", "data_buf", "spectrum", "format"])
def test_transport_history_separates_command_lock_wait_from_round_trip(client_module, queued_operation):
    async def scenario():
        first_started = asyncio.Event()
        finish_first = asyncio.Event()
        spectrum = struct.pack("<Ifff", 120, 0.0, 3.0, 0.0) + struct.pack("<1024I", *range(1024))

        async def handler(transport, request):
            command = struct.unpack_from("<H", request, 4)[0]
            if command == client_module.CMD.GET_VERSION:
                if len(transport.requests) == 1:
                    first_started.set()
                    await finish_first.wait()
                transport.reply(request, b"complete")
                return
            target = struct.unpack_from("<I", request, 8)[0]
            data = (
                spectrum if target == client_module.VS.SPECTRUM else
                b"SpecFormatVersion=1\n" if target == client_module.VS.CONFIGURATION else b""
            )
            transport.reply(request, struct.pack("<II", 1, len(data)) + data)

        client, _ = attached_client(client_module, handler)
        first = asyncio.create_task(client._execute(client_module.CMD.GET_VERSION))
        await first_started.wait()
        if queued_operation == "command":
            operation = client._execute(client_module.CMD.GET_VERSION)
        elif queued_operation == "data_buf":
            operation = client.get_data()
        elif queued_operation == "spectrum":
            operation = client.get_spectrum()
        else:
            operation = client.refresh_spectrum_format()
        second = asyncio.create_task(operation)
        await asyncio.sleep(0.025)
        finish_first.set()
        await asyncio.gather(first, second)
        history = client.transport_diagnostics["recent_commands"]
        assert len(history) == 2
        assert history[0]["lock_wait_s"] < 0.01
        assert history[1]["lock_wait_s"] >= 0.02
        assert history[1]["elapsed_s"] < history[1]["lock_wait_s"]
        assert all(command["outcome"] == "success" for command in history)

    asyncio.run(scenario())


def test_direct_spectrum_transport_failure_is_strict_and_has_no_configuration_gate(client_module):
    async def scenario():
        client, transport = attached_client(client_module, lambda *_: None)
        with pytest.raises(TimeoutError):
            await client.get_spectrum()
        assert not client._spectrum_format_loaded
        assert len(transport.requests) == 1
        assert struct.unpack_from("<I", transport.requests[0], 8)[0] == client_module.VS.SPECTRUM
        assert not client.is_connected

    asyncio.run(scenario())



def test_get_data_merges_record_decode_and_measurement_candidate_diagnostics(
    client_module, monkeypatch,
):
    async def scenario():
        client, _ = attached_client(client_module)
        client._base_time = datetime.datetime(2026, 1, 1)
        record = struct.pack("<BBBi", 0, 0, 0, 0) + struct.pack(
            "<ffHHHB", 12.5, 1.23e-4, 15, 20, 0, 0
        )
        unknown = struct.pack("<BBBi", 1, 0, 99, 0)

        async def data(vs_id):
            assert vs_id == client_module.VS.DATA_BUF
            return record + unknown

        monkeypatch.setattr(client, "_read_vs", data)
        result = await client.get_data()
        assert result.count_rate == 12.5
        assert result.data_buf_status["decoded_records"] == 1
        assert result.data_buf_status["record_types"] == {"RealTimeData": 1}
        assert result.data_buf_status["stop_reason"].startswith("unknown_record")
        assert result.data_buf_status["unread_bytes"] == len(unknown)
        assert result.data_buf_status["measurement_candidates"]["RealTimeData"]["valid_pairs"] == 1

    asyncio.run(scenario())

def test_temperature_register_is_cached_between_sensor_polls(client_module, monkeypatch):
    async def scenario():
        client, _ = attached_client(client_module)
        client._base_time = datetime.datetime(2026, 1, 1)
        temperature_reads = []
        primary_reads = []

        async def temperature(vsfr_ids):
            temperature_reads.append(vsfr_ids)
            return [22.5]

        async def data(vs_id):
            primary_reads.append(vs_id)
            return b""

        monkeypatch.setattr(client, "_read_vsfr_batch", temperature)
        monkeypatch.setattr(client, "_read_vs", data)
        assert (await client.get_data()).temperature is None
        assert not temperature_reads
        assert await client.get_temperature() == 22.5
        for _ in range(30):
            assert (await client.get_data()).temperature is None
            assert await client.get_temperature() == 22.5
        assert len(temperature_reads) == 1
        assert len(primary_reads) == 31
        client._last_temperature_read -= 60
        assert await client.get_temperature() == 22.5
        assert (await client.get_data()).temperature is None
        assert len(temperature_reads) == 2

    asyncio.run(scenario())


def test_new_rare_data_temperature_replaces_cached_value(client_module, monkeypatch):
    async def scenario():
        client, _ = attached_client(client_module)
        client._base_time = datetime.datetime(2026, 1, 1)
        client._temperature = 22.5

        async def temperature(vsfr_ids):
            raise AssertionError("RareData makes a temperature register read unnecessary")

        async def data(vs_id):
            # Real RareData record: timestamp, dose, temperature, battery.
            return struct.pack("<BBBiIfHHH", 0, 0, 3, 0, 60, 0.001, 4525, 8000, 0)

        monkeypatch.setattr(client, "_read_vsfr_batch", temperature)
        monkeypatch.setattr(client, "_read_vs", data)
        assert (await client.get_data()).temperature == 25.25
        assert client._temperature == 25.25
        assert client._last_temperature_read is not None

    asyncio.run(scenario())


def test_failed_optional_temperature_keeps_radiation_and_deadline_across_reconnect(
    client_module, monkeypatch
):
    async def scenario():
        temperature_attempts = []
        record = struct.pack("<BBBi", 0, 0, 0, 0) + struct.pack(
            "<ffHHHB", 12.5, 1.23e-4, 15, 20, 0, 0
        )

        def handler(transport, request):
            cmd = struct.unpack_from("<H", request, 4)[0]
            if cmd == client_module.CMD.RD_VIRT_SFR_BATCH:
                temperature_attempts.append(request)
                transport.notify(struct.pack("<i", 80) + request[4:8])
            elif cmd == client_module.CMD.RD_VIRT_STRING:
                transport.reply(request, struct.pack("<II", 1, len(record)) + record)
            else:
                transport.reply_success(transport, request)

        client, old = attached_client(client_module, handler)
        client._base_time = datetime.datetime(2026, 1, 1)
        first = await client.get_data()
        assert first.count_rate == 12.5
        assert first.dose_rate == pytest.approx(1.23)
        assert first.measurement_time == datetime.datetime(2026, 1, 1)
        assert first.measurement_type == "RealTimeData"
        assert first.measurement_flags == 0
        assert not temperature_attempts
        assert client.is_connected
        with pytest.raises(TimeoutError):
            await client.get_temperature()
        assert first.count_rate == 12.5  # already delivered primary reading
        assert len(temperature_attempts) == 1
        assert not client.is_connected
        deadline = client._last_temperature_read

        fresh = FakeBLETransport(client_module, handler)
        device = supply_connection(monkeypatch, client_module, fresh)
        await client.connect(device)
        assert client._last_temperature_read == deadline
        for _ in range(20):
            assert (await client.get_data()).count_rate == 12.5
            await client.get_temperature()
        assert len(temperature_attempts) == 1
        assert client.is_connected
        client._last_temperature_read -= 60
        assert (await client.get_data()).count_rate == 12.5
        with pytest.raises(TimeoutError):
            await client.get_temperature()
        assert len(temperature_attempts) == 2
        assert not client.is_connected

    asyncio.run(scenario())


def test_disconnect_during_old_teardown_prevents_new_establishment(
    client_module, monkeypatch
):
    async def scenario():
        client, old = attached_client(client_module)
        cleanup_started = asyncio.Event()
        finish_cleanup = asyncio.Event()

        async def slow_disconnect():
            old.disconnect_count += 1
            cleanup_started.set()
            await finish_cleanup.wait()
            old.is_connected = False

        old.disconnect = slow_disconnect
        fresh = FakeBLETransport(client_module)
        device = supply_connection(monkeypatch, client_module, fresh)
        task = asyncio.create_task(client.connect(device))
        await cleanup_started.wait()
        await client.disconnect()
        finish_cleanup.set()
        with pytest.raises(ConnectionError, match="cancelled by disconnect"):
            await task
        assert old.disconnect_count == 1
        assert fresh.notify_callback is None
        assert not fresh.requests
        assert not client.is_connected

    asyncio.run(scenario())


def test_disconnect_during_establishment_releases_new_client_without_init(
    client_module, monkeypatch
):
    async def scenario():
        client = client_module.RadiaCodeBLEClient()
        fresh = FakeBLETransport(client_module)
        connecting = asyncio.Event()
        finish_connect = asyncio.Event()

        async def establish(_type, _device, _name, **kwargs):
            fresh.disconnected_callback = kwargs["disconnected_callback"]
            connecting.set()
            await finish_connect.wait()
            return fresh

        monkeypatch.setattr(client_module, "establish_connection", establish)
        device = types.SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
        task = asyncio.create_task(client.connect(device))
        await connecting.wait()
        await client.disconnect()
        finish_connect.set()
        with pytest.raises(ConnectionError, match="cancelled by disconnect"):
            await task
        assert fresh.disconnect_count == 1
        assert fresh.notify_callback is None
        assert not fresh.requests
        assert not client.is_connected

    asyncio.run(scenario())


def test_disconnect_invalidates_reconnect_already_waiting_for_command_lock(
    client_module, monkeypatch
):
    async def scenario():
        client, old = attached_client(client_module, lambda *_: None)
        fresh = FakeBLETransport(client_module)
        device = supply_connection(monkeypatch, client_module, fresh)
        command = asyncio.create_task(client._execute(client_module.CMD.GET_VERSION))
        await old.write_started.wait()
        reconnect = asyncio.create_task(client.connect(device))
        await asyncio.sleep(0)  # reconnect captures generation before blocking
        await client.disconnect()
        with pytest.raises(ConnectionError):
            await command
        with pytest.raises(ConnectionError, match="cancelled by disconnect"):
            await reconnect
        assert fresh.notify_callback is None
        assert not fresh.requests
        assert not client.is_connected

    asyncio.run(scenario())


@pytest.mark.parametrize("split", [1, 2, 3])
def test_length_header_split_across_notifications_is_reassembled(client_module, split):
    async def scenario():
        payload = bytes(range(256))

        def fragmented_header(transport, request):
            frame = struct.pack("<i", 4 + len(payload)) + request[4:8] + payload
            transport.notify(frame[:split])
            for offset in range(split, len(frame), 20):
                transport.notify(frame[offset:offset + 20])

        client, transport = attached_client(client_module, fragmented_header)
        assert await client._execute(client_module.CMD.GET_VERSION) == payload
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["received_header_bytes"] == 4
        assert stats["missing_header_bytes"] == 0
        assert stats["received_body_bytes"] == 4 + len(payload)
        assert stats["missing_body_bytes"] == 0
        assert transport.disconnect_count == 0

    asyncio.run(scenario())


def test_header_echo_and_body_can_arrive_one_byte_at_a_time(client_module):
    async def scenario():
        payload = b"measurement"

        def bytewise(transport, request):
            frame = struct.pack("<i", 4 + len(payload)) + request[4:8] + payload
            for byte in frame:
                transport.notify(bytes([byte]))

        client, transport = attached_client(client_module, bytewise)
        assert await client._execute(client_module.CMD.GET_VERSION) == payload
        assert client.transport_diagnostics["recent_commands"][-1]["notification_count"] == 19
        assert transport.disconnect_count == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("header_bytes", [1, 2, 3])
def test_partial_header_stalls_without_accepting_or_replaying_data_buf(
    client_module, header_bytes,
):
    async def scenario():
        def partial(transport, request):
            transport.notify(struct.pack("<i", 40)[:header_bytes])

        client, transport = attached_client(client_module, partial)
        with pytest.raises(TimeoutError, match="Incomplete response"):
            await client._read_vs(client_module.VS.DATA_BUF)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["error_category"] == "incomplete_response"
        assert stats["received_header_bytes"] == header_bytes
        assert stats["missing_header_bytes"] == 4 - header_bytes
        assert stats["declared_body_bytes"] is None
        assert stats["received_body_bytes"] == 0
        assert stats["missing_body_bytes"] is None
        assert len(transport.requests) == 1
        assert transport.disconnect_count == 1
        assert not client.is_connected

    asyncio.run(scenario())


def test_empty_notifications_do_not_renew_partial_header_stall(client_module):
    async def scenario():
        tasks = []

        def partial_with_empty_keepalives(transport, request):
            transport.notify(b"\x20")

            async def empty_notifications():
                for _ in range(10):
                    await asyncio.sleep(0.004)
                    transport.notify(b"")

            tasks.append(asyncio.create_task(empty_notifications()))

        client, transport = attached_client(client_module, partial_with_empty_keepalives)
        with pytest.raises(TimeoutError):
            await client._execute(client_module.CMD.GET_VERSION)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["notification_count"] < 11
        assert stats["error_category"] == "incomplete_response"
        await asyncio.gather(*tasks)
        assert transport.disconnect_count == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("declared", [65536, 390103040])
def test_prehandshake_oversize_length_is_rejected_before_buffering_body(
    client_module, declared,
):
    async def scenario():
        def oversized(transport, request):
            header = struct.pack("<i", declared)
            transport.notify(header[:1])
            transport.notify(header[1:] + request[4:8] + b"untrusted body")

        client, transport = attached_client(client_module, oversized)
        with pytest.raises(ValueError, match="exceeds limit 65535"):
            await client._execute(client_module.CMD.GET_VERSION)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["declared_body_bytes"] == declared
        assert stats["error_category"] == "invalid_frame"
        assert stats["received_body_bytes"] == 0
        assert len(client._resp_buf) == 0
        assert transport.disconnect_count == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("interface", [None, 0x0201])
def test_set_exchange_retains_legacy_and_current_negotiated_limits(
    client_module, monkeypatch, interface,
):
    async def scenario():
        target = 0xDADA0107
        handshake = struct.pack("<IHH", target, 2048, 1024)
        if interface is not None:
            handshake += struct.pack("<I", interface)

        def handler(transport, request):
            cmd = struct.unpack_from("<H", request, 4)[0]
            if cmd == client_module.CMD.SET_EXCHANGE:
                assert request[8:] == b"\x01\xff\x12\xff"
                transport.reply(request, handshake, packet_size=1)
            else:
                transport.reply_success(transport, request)

        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(client_module, handler)
        device = supply_connection(monkeypatch, client_module, transport)
        await client.connect(device)
        metadata = client.transport_diagnostics["transport"]
        assert metadata["exchange"] == {
            "target_id": target,
            "detector_tx_size": 2048,
            "detector_rx_size": 1024,
            "interface_ready": None if interface is None else True,
            "interface_type": None if interface is None else 2,
        }
        assert metadata["response_body_limit"] == 2048
        assert len(transport.requests) == 5
        assert all(mode is False for mode in transport.write_responses)
        metadata["exchange"]["target_id"] = 0
        assert client.transport_diagnostics["transport"]["exchange"]["target_id"] == target
        await client.disconnect()
        assert client.transport_diagnostics["transport"]["exchange"] == {}
        assert client.transport_diagnostics["transport"]["response_body_limit"] == 65535

    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [
    b"", b"\x01" * 4, b"\x01" * 7, b"\x01" * 9, b"\x01" * 11,
    struct.pack("<IHH", 0xFF03FF33, 0, 1024),
    struct.pack("<IHH", 0xFF03FF33, 3, 1024),
    struct.pack("<IHH", 0xFF03FF33, 1024, 0),
    struct.pack("<IHH", 0xFF03FF33, 1024, 3),
])
def test_invalid_set_exchange_fails_init_and_clears_limits(
    client_module, monkeypatch, payload,
):
    async def scenario():
        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(
            client_module, lambda transport, request: transport.reply(request, payload),
        )
        device = supply_connection(monkeypatch, client_module, transport)
        with pytest.raises(client_module.RadiaCodeInitError) as error:
            await client.connect(device)
        assert error.value.step == "set_exchange"
        assert isinstance(error.value.__cause__, ValueError)
        assert len(transport.requests) == 1
        assert transport.disconnect_count == 1
        diagnostics = client.transport_diagnostics
        assert diagnostics["transport"]["exchange"] == {}
        assert diagnostics["transport"]["response_body_limit"] == 65535
        assert diagnostics["recent_commands"][-1]["error_category"] == "invalid_payload"
        assert diagnostics["initialization_steps"][-1]["outcome"] == "ValueError"

    asyncio.run(scenario())


def test_negotiated_limit_accepts_boundary_and_rejects_oversize_reply(
    client_module, monkeypatch,
):
    async def scenario():
        payloads = [b"x" * 60, b"x" * 61]

        def handler(transport, request):
            cmd = struct.unpack_from("<H", request, 4)[0]
            if cmd == client_module.CMD.SET_EXCHANGE:
                transport.reply(request, struct.pack("<IHHI", 0xFF03FF33, 64, 128, 0x0201))
            elif cmd == client_module.CMD.GET_VERSION:
                transport.reply(request, payloads.pop(0))
            else:
                transport.reply_success(transport, request)

        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(client_module, handler)
        device = supply_connection(monkeypatch, client_module, transport)
        await client.connect(device)
        assert await client._execute(client_module.CMD.GET_VERSION) == b"x" * 60
        with pytest.raises(ValueError, match="body length 65 exceeds limit 64"):
            await client._execute(client_module.CMD.GET_VERSION)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["response_body_limit"] == 64
        assert stats["declared_body_bytes"] == 65
        assert stats["received_body_bytes"] == 0
        assert stats["error_category"] == "invalid_frame"
        assert transport.disconnect_count == 1
        assert len(transport.requests) == 7

    asyncio.run(scenario())


def test_reconnect_discards_previous_limits_and_partial_header(
    client_module, monkeypatch,
):
    async def scenario():
        def first_handler(transport, request):
            cmd = struct.unpack_from("<H", request, 4)[0]
            if cmd == client_module.CMD.SET_EXCHANGE:
                transport.reply(request, struct.pack("<IHHI", 0xFF03FF33, 12, 128, 0x0201))
            elif cmd == client_module.CMD.GET_VERSION:
                transport.notify(b"\x08\x00")
            else:
                transport.reply_success(transport, request)

        client = client_module.RadiaCodeBLEClient()
        first = FakeBLETransport(client_module, first_handler)
        device = supply_connection(monkeypatch, client_module, first)
        await client.connect(device)
        assert client.transport_diagnostics["transport"]["response_body_limit"] == 12
        with pytest.raises(TimeoutError):
            await client._execute(client_module.CMD.GET_VERSION)
        fresh = FakeBLETransport(client_module)
        supply_connection(monkeypatch, client_module, fresh)
        await client.connect(device)
        assert client.transport_diagnostics["transport"]["response_body_limit"] == 65535
        assert client.transport_diagnostics["transport"]["exchange"]["detector_tx_size"] == 65535
        assert await client._execute(client_module.CMD.GET_VERSION) == struct.pack("<I", 1)
        first.notify(b"\x00\x00garbage")
        first.drop_link()
        assert client.is_connected
        assert client.transport_diagnostics["transport"]["exchange"]["detector_tx_size"] == 65535
        await client.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("acknowledged", [False, True])
def test_requested_write_mode_is_applied_to_all_chunks(client_module, acknowledged):
    async def scenario():
        client, transport = attached_client(
            client_module,
            lambda transport, request: transport.reply(request, b"accepted"),
            acknowledged_writes=acknowledged,
        )
        assert client.transport_diagnostics["transport"]["write_mode_effective"] is None
        assert await client._execute(client_module.CMD.GET_VERSION, b"x" * 45) == b"accepted"
        mode = "with_response" if acknowledged else "without_response"
        assert transport.write_responses == [acknowledged] * 3
        assert client.transport_diagnostics["transport"]["write_mode_requested"] == mode
        assert client.transport_diagnostics["transport"]["write_mode_effective"] == mode
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["write_mode_requested"] == mode
        assert stats["write_mode_effective"] == mode
        assert stats["completed_write_chunks"] == 3
        assert transport.disconnect_count == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("properties", [None, [], ["write-without-response"]])
def test_acknowledged_mode_validates_write_support_before_any_command(
    client_module, monkeypatch, properties,
):
    async def scenario():
        client = client_module.RadiaCodeBLEClient(acknowledged_writes=True)
        transport = FakeBLETransport(client_module)
        transport.services.characteristic.properties = properties
        device = supply_connection(monkeypatch, client_module, transport)
        with pytest.raises(client_module.RadiaCodeInitError) as error:
            await client.connect(device)
        assert error.value.step == "write_mode"
        assert "not supported" in str(error.value)
        assert not transport.writes
        assert not transport.requests
        assert transport.notify_callback is None
        assert transport.disconnect_count == 1
        assert client.transport_diagnostics["transport"]["write_mode_requested"] == "with_response"
        assert client.transport_diagnostics["transport"]["write_mode_effective"] is None

    asyncio.run(scenario())


def test_default_unacknowledged_mode_accepts_characteristic_without_write_support(
    client_module, monkeypatch,
):
    async def scenario():
        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(client_module)
        transport.services.characteristic.properties = ["write-without-response"]
        device = supply_connection(monkeypatch, client_module, transport)
        await client.connect(device)
        assert len(transport.requests) == 5
        assert all(response is False for response in transport.write_responses)
        await client.disconnect()

    asyncio.run(scenario())


def test_acknowledged_write_stall_with_complete_reply_never_falls_back_or_replays(
    client_module,
):
    async def scenario():
        write_cancelled = asyncio.Event()

        async def reply_without_write_ack(transport, request):
            transport.reply(request, struct.pack("<II", 1, 0))
            try:
                await asyncio.Event().wait()
            finally:
                write_cancelled.set()

        client, transport = attached_client(
            client_module, reply_without_write_ack, acknowledged_writes=True,
        )
        with pytest.raises(TimeoutError):
            await client._read_vs(client_module.VS.DATA_BUF)
        stats = client.transport_diagnostics["recent_commands"][-1]
        assert stats["error_category"] == "write_timeout"
        assert stats["write_mode_requested"] == "with_response"
        assert stats["write_mode_effective"] == "with_response"
        assert stats["received_body_bytes"] == 12
        assert stats["missing_body_bytes"] == 0
        assert len(transport.requests) == 1
        assert transport.write_responses == [True]
        assert write_cancelled.is_set()
        assert transport.disconnect_count == 1
        assert not client.is_connected

    asyncio.run(scenario())


def test_acknowledged_write_failure_does_not_silently_change_mode(client_module):
    async def scenario():
        def unsupported(*_):
            raise RuntimeError("proxy rejected acknowledged write")

        client, transport = attached_client(
            client_module, unsupported, acknowledged_writes=True,
        )
        with pytest.raises(RuntimeError, match="proxy rejected acknowledged write"):
            await client._execute(client_module.CMD.GET_VERSION)
        assert transport.write_responses == [True]
        assert len(transport.requests) == 1
        assert transport.disconnect_count == 1
        assert client.transport_diagnostics["recent_commands"][-1]["error_category"] == "write_error"

    asyncio.run(scenario())


# Minimal protocol schema supplied by the detector; unrelated capabilities are
# unnecessary for establishing the six-byte group-4 record layout.
_MODERN_RECORD_CONFIGURATION = """[DataStructure]
[[GRP_DoseCounter]]
Id=4
[[[CHN_DoseCounter]]]
Id=22
Unit=" uR| мкР"
ScaledUnit=1
DType=8
RType=8
[[[CHN_Flags]]]
Id=0
DType=4
RType=8
"""


def _live_sensor_record(seq=0, offset=0, count_rate=12.5):
    return struct.pack("<BBBi", seq, 0, 0, offset) + struct.pack(
        "<ffHHHB", count_rate, 0.00002, 10, 20, 0, 0
    )


def _dose_counter_record(seq=0, offset=0):
    return struct.pack("<BBBiIH", seq, 0, 4, offset, 106009, 0x3000)


def _sensor_configuration_handler(module, config, records=b"", spectrum=None):
    """Answer real virtual-string frames and reject unrequested bulk work."""
    def handler(transport, request):
        command = struct.unpack_from("<H", request, 4)[0]
        if command != module.CMD.RD_VIRT_STRING:
            transport.reply_success(transport, request)
            return
        target = struct.unpack_from("<I", request, 8)[0]
        if target == module.VS.CONFIGURATION:
            data = config.encode("cp1251")
        elif target == module.VS.DATA_BUF:
            data = records
        elif target in (module.VS.SPECTRUM, module.VS.SPEC_ACCUM) and spectrum is not None:
            data = spectrum
        else:
            raise AssertionError(f"Unexpected virtual-string read: {target:#x}")
        transport.reply(request, struct.pack("<II", 1, len(data)) + data)

    return handler


def _virtual_string_requests(module, transport):
    return [
        struct.unpack_from("<I", request, 8)[0]
        for request in transport.requests
        if struct.unpack_from("<H", request, 4)[0] == module.CMD.RD_VIRT_STRING
    ]


def test_initial_configuration_decodes_counter_and_following_live_without_spectrum(
    client_module, monkeypatch,
):
    async def scenario():
        records = _dose_counter_record() + _live_sensor_record(1, 1)
        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(
            client_module,
            _sensor_configuration_handler(client_module, _MODERN_RECORD_CONFIGURATION, records),
        )
        device = supply_connection(monkeypatch, client_module, transport)
        await client.connect(device)
        assert _virtual_string_requests(client_module, transport) == [
            client_module.VS.CONFIGURATION, client_module.VS.DATA_BUF,
        ]
        assert [step["step"] for step in client.transport_diagnostics["initialization_steps"]] == [
            "establish_connection", "start_notify", "set_exchange", "set_time",
            "device_time", "configuration", "data_buf",
        ]
        schema = client.transport_diagnostics["configuration"]
        assert schema["loaded"] is True
        assert schema["group4_schema"] == client_module.Group4Format.DOSE_COUNTER.value
        assert not client.configuration_retry_due

        data = await client.get_data()
        assert data.dose_counter == pytest.approx(1060.09)
        assert data.accumulated_dose is None
        assert data.count_rate == pytest.approx(12.5)
        assert data.dose_rate == pytest.approx(0.2)
        assert data.measurement_type == "RealTimeData"
        assert data.measurement_time == client._base_time + datetime.timedelta(milliseconds=10)
        assert data.data_buf_status["stop_reason"] == "complete"
        assert data.data_buf_status["record_types"] == {
            "DoseCounterData": 1, "RealTimeData": 1,
        }
        assert _virtual_string_requests(client_module, transport) == [
            client_module.VS.CONFIGURATION, client_module.VS.DATA_BUF, client_module.VS.DATA_BUF,
        ]
        assert client.is_connected
        assert transport.disconnect_count == 0
        await client.disconnect()

    asyncio.run(scenario())


def test_complete_record_configuration_is_cached_across_same_address_reconnect(
    client_module, monkeypatch,
):
    async def scenario():
        records = _dose_counter_record() + _live_sensor_record(1, 1)
        client = client_module.RadiaCodeBLEClient()
        handler = _sensor_configuration_handler(
            client_module, _MODERN_RECORD_CONFIGURATION, records,
        )
        first = FakeBLETransport(client_module, handler)
        device = supply_connection(monkeypatch, client_module, first)
        await client.connect(device)
        assert len(first.requests) == 5
        assert (await client.get_data()).dose_counter == pytest.approx(1060.09)

        second = FakeBLETransport(client_module, handler)
        supply_connection(monkeypatch, client_module, second)
        # Address normalization must not discard a valid schema.
        await client.connect(types.SimpleNamespace(address=device.address.lower()))
        assert first.disconnect_count == 1
        assert len(second.requests) == 4
        assert _virtual_string_requests(client_module, second) == [client_module.VS.DATA_BUF]
        assert (await client.get_data()).dose_counter == pytest.approx(1060.09)
        assert _virtual_string_requests(client_module, second) == [
            client_module.VS.DATA_BUF, client_module.VS.DATA_BUF,
        ]
        assert client.transport_diagnostics["configuration"]["group4_schema"] == (
            client_module.Group4Format.DOSE_COUNTER.value
        )
        await client.disconnect()

    asyncio.run(scenario())


def test_different_address_invalidates_the_record_configuration_cache(
    client_module, monkeypatch,
):
    async def scenario():
        client = client_module.RadiaCodeBLEClient()
        first = FakeBLETransport(
            client_module,
            _sensor_configuration_handler(client_module, _MODERN_RECORD_CONFIGURATION),
        )
        device = supply_connection(monkeypatch, client_module, first)
        await client.connect(device)
        assert client._group4_format is client_module.Group4Format.DOSE_COUNTER

        unknown_handler = _sensor_configuration_handler(
            client_module, "[DeviceParams]\nFirmware=unknown\n",
            _dose_counter_record() + _live_sensor_record(1, 1),
        )

        def second_handler(transport, request):
            command = struct.unpack_from("<H", request, 4)[0]
            if command == client_module.CMD.SET_EXCHANGE:
                assert client._group4_format is None
                assert not client._configuration_loaded
            unknown_handler(transport, request)

        second = FakeBLETransport(client_module, second_handler)
        supply_connection(monkeypatch, client_module, second)
        await client.connect(types.SimpleNamespace(address="11:22:33:44:55:66"))
        assert len(second.requests) == 5
        assert _virtual_string_requests(client_module, second) == [
            client_module.VS.CONFIGURATION, client_module.VS.DATA_BUF,
        ]
        assert client.transport_diagnostics["configuration"]["loaded"] is True
        assert client.transport_diagnostics["configuration"]["group4_schema"] is None
        data = await client.get_data()
        assert data.dose_counter is None
        assert data.count_rate is None
        assert data.measurement_time is None
        assert data.data_buf_status["stop_reason"] == "unsupported_group4_schema"
        assert client.is_connected
        await client.disconnect()

    asyncio.run(scenario())


def test_incomplete_configuration_retires_link_and_backs_off_until_optional_retry(
    client_module, monkeypatch,
):
    async def scenario():
        clock = [100.0]
        # Replace only this client's clock module, preserving asyncio's clock.
        monkeypatch.setattr(client_module, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
        client = client_module.RadiaCodeBLEClient()

        def incomplete_configuration(transport, request):
            command = struct.unpack_from("<H", request, 4)[0]
            if command == client_module.CMD.RD_VIRT_STRING:
                assert struct.unpack_from("<I", request, 8)[0] == client_module.VS.CONFIGURATION
                transport.notify(struct.pack("<i", 80) + request[4:8] + b"partial")
            else:
                transport.reply_success(transport, request)

        first = FakeBLETransport(client_module, incomplete_configuration)
        device = supply_connection(monkeypatch, client_module, first)
        with pytest.raises(client_module.RadiaCodeInitError) as error:
            await client.connect(device)
        assert error.value.step == "configuration"
        assert isinstance(error.value.__cause__, TimeoutError)
        assert first.disconnect_count == 1
        assert len(first.requests) == 4
        assert _virtual_string_requests(client_module, first) == [client_module.VS.CONFIGURATION]
        assert not client.is_connected
        assert not client._cmd_lock.locked()
        assert not client.configuration_retry_due
        assert client.transport_diagnostics["configuration"] == {
            "loaded": False, "group4_schema": None,
            "retry_in_seconds": client_module._CONFIGURATION_RETRY_INTERVAL,
        }

        # Recovery must perform small init/drain commands and deliver live
        # samples while the failed optional bulk read remains in backoff.
        fresh = FakeBLETransport(
            client_module,
            _sensor_configuration_handler(
                client_module, _MODERN_RECORD_CONFIGURATION, _live_sensor_record(),
            ),
        )
        supply_connection(monkeypatch, client_module, fresh)
        await client.connect(device)
        assert len(fresh.requests) == 4
        assert _virtual_string_requests(client_module, fresh) == [client_module.VS.DATA_BUF]
        assert (await client.get_data()).count_rate == pytest.approx(12.5)
        before_retry = len(fresh.requests)
        await client.refresh_record_configuration()
        assert len(fresh.requests) == before_retry
        clock[0] += client_module._CONFIGURATION_RETRY_INTERVAL - 0.001
        assert not client.configuration_retry_due
        await client.refresh_record_configuration()
        assert len(fresh.requests) == before_retry

        clock[0] += 0.001
        assert client.configuration_retry_due
        await client.refresh_record_configuration()
        assert len(fresh.requests) == before_retry + 1
        assert _virtual_string_requests(client_module, fresh).count(client_module.VS.CONFIGURATION) == 1
        assert not client.configuration_retry_due
        assert client.transport_diagnostics["configuration"]["group4_schema"] == (
            client_module.Group4Format.DOSE_COUNTER.value
        )
        fresh.handler = _sensor_configuration_handler(
            client_module, _MODERN_RECORD_CONFIGURATION,
            _dose_counter_record() + _live_sensor_record(1, 1),
        )
        data = await client.get_data()
        assert data.count_rate == pytest.approx(12.5)
        assert data.dose_counter == pytest.approx(1060.09)
        assert data.data_buf_status["stop_reason"] == "complete"
        after_retry = len(fresh.requests)
        await client.refresh_record_configuration()
        assert len(fresh.requests) == after_retry
        assert first.disconnect_count == 1
        assert fresh.disconnect_count == 0
        assert client.is_connected
        await client.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("configuration", [
    "", "[DataStructure]\n[[GRP_DoseCounter]]\nId=4\n",
    _MODERN_RECORD_CONFIGURATION.replace("DType=8", "DType=9"),
    _MODERN_RECORD_CONFIGURATION + _MODERN_RECORD_CONFIGURATION,
])
def test_unknown_complete_schema_preserves_prior_live_record_and_stops_at_group4(
    client_module, monkeypatch, configuration,
):
    async def scenario():
        records = (
            _live_sensor_record(0, 0, count_rate=5.0)
            + _dose_counter_record(1, 1)
            + _live_sensor_record(2, 2, count_rate=50.0)
        )
        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(
            client_module, _sensor_configuration_handler(client_module, configuration, records),
        )
        device = supply_connection(monkeypatch, client_module, transport)
        await client.connect(device)
        schema = client.transport_diagnostics["configuration"]
        assert schema["loaded"] is True
        assert schema["group4_schema"] is None
        assert not client.configuration_retry_due
        data = await client.get_data()
        assert data.count_rate == pytest.approx(5.0)
        assert data.dose_counter is None
        assert data.accumulated_dose is None
        assert data.measurement_time == client._base_time
        assert data.data_buf_status["stop_reason"] == "unsupported_group4_schema"
        assert data.data_buf_status["record_types"] == {"RealTimeData": 1}
        assert data.data_buf_status["decoded_records"] == 1
        assert _virtual_string_requests(client_module, transport).count(client_module.VS.CONFIGURATION) == 1
        assert client.is_connected
        await client.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("spectrum_declaration", [
    "", "SpecFormatVersion=abc\n", "SpecFormatVersion=\n", "SpecFormatVersion=2\n",
    "SpecFormatVersion=0\nSpecFormatVersion=1\n",
])
def test_record_schema_discovery_does_not_poison_observed_spectrum_encoding(
    client_module, monkeypatch, spectrum_declaration,
):
    async def scenario():
        spectrum = struct.pack("<Ifff", 120, 0.0, 3.0, 0.0) + b"\x00\x40"
        handler = _sensor_configuration_handler(
            client_module, _MODERN_RECORD_CONFIGURATION + spectrum_declaration,
            _dose_counter_record() + _live_sensor_record(1, 1), spectrum,
        )
        client, first = attached_client(client_module, handler)
        assert (await client.get_spectrum()).counts == [0] * 1024
        assert client.spectrum_format_version == 1
        assert client.spectrum_format_source == "complete_payload"
        assert _virtual_string_requests(client_module, first) == [client_module.VS.SPECTRUM]

        fresh = FakeBLETransport(client_module, handler)
        device = supply_connection(monkeypatch, client_module, fresh)
        await client.connect(device)
        assert client.spectrum_format_version == 1
        assert client.spectrum_format_source == "complete_payload"
        assert not client._spectrum_format_authoritative
        assert client.transport_diagnostics["configuration"]["group4_schema"] == (
            client_module.Group4Format.DOSE_COUNTER.value
        )
        assert (await client.get_data()).dose_counter == pytest.approx(1060.09)
        before_spectrum = len(fresh.requests)
        assert (await client.get_spectrum()).counts == [0] * 1024
        assert len(fresh.requests) == before_spectrum + 1
        assert _virtual_string_requests(client_module, fresh).count(client_module.VS.CONFIGURATION) == 1
        assert _virtual_string_requests(client_module, fresh)[-1] == client_module.VS.SPECTRUM
        assert client.spectrum_format_version == 1
        assert client.is_connected
        await client.disconnect()

    asyncio.run(scenario())
