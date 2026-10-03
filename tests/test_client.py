"""BLE transport tests with real protocol frames and a dependency-free fake.

These cover framing, proxy/backend stalls and lifecycle races.  They do not
simulate detector firmware load or validate a physical Bluetooth radio.
"""

import asyncio
import datetime
import importlib.util
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
    return module


class FakeBLETransport:
    """Record writes and deliver controlled responses from the same loop."""

    def __init__(self, module, handler=None):
        self.module = module
        self.handler = handler or self.reply_success
        self.is_connected = True
        self.services = [types.SimpleNamespace(uuid=module.SERVICE_UUID)]
        self.notify_callback = None
        self.disconnected_callback = None
        self.disconnect_count = 0
        self.requests = []
        self.writes = []
        self.pending = bytearray()
        self.write_started = asyncio.Event()

    async def start_notify(self, characteristic, callback):
        assert characteristic == self.module.NOTIFY_CHAR_UUID
        self.notify_callback = callback

    async def write_gatt_char(self, characteristic, chunk, *, response):
        assert characteristic == self.module.WRITE_CHAR_UUID
        assert response is False
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
        if cmd == self.module.CMD.RD_VIRT_STRING:
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


def attached_client(module, handler=None):
    client = module.RadiaCodeBLEClient()
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
        (b"\x04\x00", "header too short"),
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
        assert len(fresh.requests) == 4  # handshake, time, device time, drain

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
            if cmd == client_module.CMD.RD_VIRT_STRING:
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


def test_spectrum_reads_configuration_once_per_connection(client_module, monkeypatch):
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
            if vs_id == client_module.VS.CONFIGURATION:
                data = b"Firmware=4.14\n"  # upstream default when key is absent
            elif vs_id == client_module.VS.DATA_BUF:
                data = b""
            else:
                data = spectrum
            transport.reply(request, struct.pack("<II", 1, len(data)) + data)

        client, transport = attached_client(client_module, handler)
        assert client.spectrum_format_version is None
        assert len((await client.get_spectrum()).counts) == 1024
        assert len((await client.get_spectrum(accumulated=True)).counts) == 1024
        assert client.spectrum_format_version == 0
        assert calls == [
            client_module.VS.CONFIGURATION,
            client_module.VS.SPECTRUM,
            client_module.VS.SPEC_ACCUM,
        ]
        fresh = FakeBLETransport(client_module, handler)
        device = supply_connection(monkeypatch, client_module, fresh)
        await client.connect(device)
        assert transport.disconnect_count == 1
        assert client.spectrum_format_version is None
        assert not client._spectrum_format_loaded
        assert len((await client.get_spectrum()).counts) == 1024
        assert calls.count(client_module.VS.CONFIGURATION) == 2

    asyncio.run(scenario())


def test_full_frame_with_incomplete_spectrum_is_rejected(client_module):
    async def scenario():
        data = struct.pack("<Ifff", 120, 0.0, 3.0, 0.0) + b"\x00" * (512 * 4)

        def handler(transport, request):
            transport.reply(request, struct.pack("<II", 1, len(data)) + data)

        client, _ = attached_client(client_module, handler)
        client._spectrum_format_loaded = True
        client._spectrum_format_version = 0
        with pytest.raises(ValueError, match="512 of 1024"):
            await client.get_spectrum()
        assert client.is_connected  # complete frame; invalid spectrum payload

    asyncio.run(scenario())


def test_configuration_transport_error_prevents_spectrum_command(client_module):
    async def scenario():
        client, transport = attached_client(client_module, lambda *_: None)
        with pytest.raises(TimeoutError):
            await client.get_spectrum()
        assert not client._spectrum_format_loaded
        assert len(transport.requests) == 1
        assert struct.unpack_from("<I", transport.requests[0], 8)[0] == client_module.VS.CONFIGURATION
        assert not client.is_connected

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
        for _ in range(30):
            assert (await client.get_data()).temperature == 22.5
        assert len(temperature_reads) == 1
        assert len(primary_reads) == 30
        client._last_temperature_read -= 60
        assert (await client.get_data()).temperature == 22.5
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
        assert len(temperature_attempts) == 1
        assert not client.is_connected
        deadline = client._last_temperature_read

        fresh = FakeBLETransport(client_module, handler)
        device = supply_connection(monkeypatch, client_module, fresh)
        await client.connect(device)
        assert client._last_temperature_read == deadline
        for _ in range(20):
            assert (await client.get_data()).count_rate == 12.5
        assert len(temperature_attempts) == 1
        assert client.is_connected
        client._last_temperature_read -= 60
        assert (await client.get_data()).count_rate == 12.5
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
