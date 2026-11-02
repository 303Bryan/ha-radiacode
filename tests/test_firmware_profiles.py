"""Vendor declarations plus explicit synthetic records; never live BLE."""
import asyncio
import datetime
import hashlib
import json
import struct
from pathlib import Path

import pytest

from test_client import client_module, FakeBLETransport, supply_connection, _sensor_configuration_handler, _virtual_string_requests
from test_scalar_schema import frames

FIXTURES = Path(__file__).parent / "fixtures/firmware"
CONFIGS = sorted(FIXTURES.glob("*4.15*.txt"))
BASE = datetime.datetime(2026, 1, 1)
BUILD = "Oct 15 2026 10:18:10"


def version_reply(build=BUILD, minor=15, boot="Feb  6 2023 15:49:14"):
    boot = boot.encode("ascii")
    target = build.encode("ascii") + b"\0"
    return struct.pack("<HHB", 0, 4, len(boot)) + boot + struct.pack("<HHB", minor, 4, len(target)) + target


def measured_record(seq=0):
    return struct.pack("<BBBi", seq, 0, 5, 0) + struct.pack("<ffHHHBBBH", 12.5, .00002, 47, 150, 60, 1, 2, 0, 64)


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_all_vendor_415_layouts_and_following_records(protocol, path):
    config = path.read_text()
    schemas = protocol.parse_scalar_schemas(config)
    assert set(schemas) == {0, 1, 2, 3, 5}
    assert all(schemas.values())
    assert protocol.parse_group4_format(config) == protocol.Group4Format.DOSE_COUNTER
    assert protocol.parse_event_schema(config) is not None
    assert next(f.unit for f in schemas[0].fields if f.channel_id == 4) == "rem/h"
    status = {}
    # Explicitly synthetic sequence: session, live, rare, raw, database, counter.
    raw = measured_record(254) + frames(True) + struct.pack("<BBBiIH", 3, 0, 4, 400, 12345, 16)
    records = protocol.decode_data_buf(raw, BASE, status, scalar_schemas=schemas,
        group4_format=protocol.parse_group4_format(config))
    assert status["stop_reason"] == "complete" and status["unread_bytes"] == 0
    assert len(records) == 5
    assert records[0].real_time_flags == 258
    values = protocol.extract_sensor_values(records)
    assert values.dose_rate == pytest.approx(.2)
    assert values.accumulated_dose == pytest.approx(1)
    assert values.dose_counter == pytest.approx(123.45)
    assert values.battery == 87.5
    # Session-only traffic must not produce or renew a live measurement.
    session = protocol.decode_data_buf(measured_record(), BASE, scalar_schemas=schemas)
    assert session == []
    assert protocol.extract_sensor_values(session).measurement_time is None
    # No truncated session can consume/publish partial fields as a live sample.
    for length in range(7, 26):
        partial = {}
        assert protocol.decode_data_buf(measured_record()[:length], BASE, partial, scalar_schemas=schemas) == []
        assert partial["stop_reason"] != "complete"


@pytest.mark.parametrize("old,new", [
    ("Id=23", "Id=24"), ("CHN_MeasTime", "CHN_Unknown"),
    ("RType=4", "RType=8"), ("Id=5\nFlgMsk", "Id=6\nFlgMsk"),
    (' rem/h|', ' Sv/h|'), (' rem/h|', ' urem/h|'),
    ("Id=26\nDType=2", "Id=26\nDType=4"),
])
def test_invalid_measurement_declarations_fail_closed(protocol, old, new):
    config = (FIXTURES / "rc103-4.15-apple.txt").read_text()
    assert old in config
    status = {}
    records = protocol.decode_data_buf(measured_record(), BASE, status,
        scalar_schemas=protocol.parse_scalar_schemas(config.replace(old, new)))
    assert records == []
    assert status["stop_offset"] == 0
    assert status["stop_reason"] == "unsupported_scalar_schema(group=5)"


def test_real_414_capture_consumes_every_byte(protocol):
    captured = json.loads((FIXTURES / "rc102-4.14-capture.json").read_text())
    config = captured["configuration"]
    raw = bytes.fromhex(captured["data_buf_hex"])
    status = {}
    records = protocol.decode_data_buf(raw, BASE, status,
        scalar_schemas=protocol.parse_scalar_schemas(config),
        group4_format=protocol.parse_group4_format(config),
        event_schema=protocol.parse_event_schema(config), event_schema_required=True)
    assert len(raw) == 1009 and len(records) == 9
    assert status["stop_reason"] == "complete" and status["unread_bytes"] == 0
    assert protocol.parse_firmware_build(bytes.fromhex(captured["get_version_hex"])) == "Jul  7 2025 11:19:22"


def test_build_requires_complete_target_tuple(protocol):
    raw = version_reply()
    assert protocol.parse_firmware_build(raw) == BUILD
    for length in range(len(raw)):
        assert protocol.parse_firmware_build(raw[:length]) is None
    for invalid in (raw + b"extra", raw.replace(b"Oct", b"\xffct"), version_reply(build=""), version_reply(build="Oct 15\x002026 10:18:10")):
        assert protocol.parse_firmware_build(invalid) is None
    # Target, never bootloader, chooses the build.
    assert protocol.parse_firmware_build(version_reply(build="Jul  7 2025 11:19:22", boot=BUILD)) != BUILD


@pytest.mark.parametrize("profile_index", range(5))
def test_profile_matches_exact_model_and_target_build(client_module, profile_index):
    profile = client_module.PROFILES[profile_index]
    async def scenario():
        client = client_module.RadiaCodeBLEClient()
        client._configuration_address = "AA:BB:CC:DD:EE:FF"
        client._serial_number = profile.model + "-000123"
        async def execute(*args):
            return version_reply(profile.build)
        client._execute = execute
        assert await client.get_firmware_version() == "4.15"
        assert client._configuration_loaded
        assert client._configuration_source == "firmware_profile"
        assert not client.configuration_retry_due
        assert client.configuration_snapshot(client._serial_number) is None
        assert client._firmware_profile["container_sha256"] == profile.container_sha256
        config = (FIXTURES / (profile.model.lower().replace("-", "") + "-4.15-apple.txt")).read_text()
        assert hashlib.sha256(config.encode("cp1251")).hexdigest() == profile.configuration_sha256
        assert client_module.parse_scalar_schemas(config) == client._scalar_schemas
    asyncio.run(scenario())


@pytest.mark.parametrize("serial,build,minor", [
    ("RC-103-000123", "Oct 15 2026 10:18:11", 15),
    ("RC-103-000123", BUILD, 14), ("RC-103G-000123", BUILD, 15),
    ("RC-104-000123", BUILD, 15), ("RC-ZERO-000123", BUILD, 15),
    ("RC-410-000123", BUILD, 15), (None, BUILD, 15),
])
def test_unknown_identity_does_not_authorize_profile(client_module, serial, build, minor):
    async def scenario():
        client = client_module.RadiaCodeBLEClient()
        client._serial_number = serial
        async def execute(*args): return version_reply(build, minor)
        client._execute = execute
        await client.get_firmware_version()
        assert not client._configuration_loaded and client.configuration_retry_due
        assert client._firmware_profile is None
    asyncio.run(scenario())


def test_profile_reconnect_build_change_device_override_and_storage(client_module, monkeypatch):
    async def scenario():
        config = (FIXTURES / "rc103-4.15-apple.txt").read_text()
        normal = _sensor_configuration_handler(client_module, config, frames(True))
        build = [BUILD]
        def handler(transport, request):
            if struct.unpack_from("<H", request, 4)[0] == client_module.CMD.GET_VERSION:
                transport.reply(request, version_reply(build[0]))
            elif struct.unpack_from("<H", request, 4)[0] == client_module.CMD.RD_VIRT_STRING and struct.unpack_from("<I", request, 8)[0] == client_module.VS.SERIAL_NUMBER:
                serial = b"RC-103-000123\0"
                transport.reply(request, struct.pack("<II", 1, len(serial)) + serial)
            else:
                normal(transport, request)
        client = client_module.RadiaCodeBLEClient()
        transport = FakeBLETransport(client_module, handler)
        device = supply_connection(monkeypatch, client_module, transport)
        await client.connect(device)
        assert await client.get_serial_number() == "RC-103-000123"
        await client.get_firmware_version()
        assert client._configuration_source == "firmware_profile"
        assert (await client.get_data()).battery == 87.5
        await client.refresh_record_configuration()
        assert client_module.VS.CONFIGURATION not in _virtual_string_requests(client_module, transport)
        # Reconnect blocks cached profile until this connection confirms the build.
        transport = FakeBLETransport(client_module, handler)
        supply_connection(monkeypatch, client_module, transport)
        await client.connect(device)
        assert not client._record_schema_usable
        assert client._configuration_source == "firmware_profile"
        await client.get_firmware_version()
        assert client._record_schema_usable
        assert (await client.get_data()).battery == 87.5
        # Explicit complete device read overrides profile and can be persisted.
        await client.get_configuration()
        assert client._configuration_source == "device" and client._firmware_profile is None
        saved = client.configuration_snapshot("RC-103-000123")
        assert saved["firmware_build"] == BUILD and saved["parser_version"] == 2
        assert client.restore_configuration_snapshot(saved, "RC-103-000123")
        # Same numeric version, different application build invalidates the schema.
        build[0] = "Oct 15 2026 10:18:11"
        await client.get_firmware_version()
        assert not client._configuration_loaded and client.configuration_retry_due
        assert not client.restore_configuration_snapshot(saved, "RC-103-000123")
        # A complete unsupported device declaration may never be hidden by a profile.
        build[0] = BUILD
        await client.get_firmware_version()
        client._cache_record_configuration(config.replace(' rem/h|', ' Sv/h|'))
        await client.get_firmware_version()
        assert client._configuration_source == "device" and client._scalar_schemas[0] is None
        await client.disconnect()
    asyncio.run(scenario())


def test_missing_identity_or_corrupt_bundle_cannot_enable_profile(client_module, monkeypatch):
    async def scenario():
        client = client_module.RadiaCodeBLEClient()
        client._serial_number = "RC-103-000123"
        replies = [struct.pack("<HHBHHB", 0, 4, 0, 15, 4, 0), version_reply()]
        async def execute(*args): return replies.pop(0)
        client._execute = execute
        await client.get_firmware_version()
        assert not client._configuration_loaded
        monkeypatch.setattr(client_module, "RECORD_CONFIGURATION", client_module.RECORD_CONFIGURATION.replace("DType=4", "DType=2"))
        await client.get_firmware_version()
        assert not client._configuration_loaded
    asyncio.run(scenario())
