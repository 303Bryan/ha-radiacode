"""Regressions for the 4.15 field log's optional-download reconnect loop."""
import asyncio
import json

import pytest

from test_coordinator import coordinator_module, clock, make_coordinator, refresh_and_settle


def test_failed_directory_never_retries_on_same_firmware_reconnect(make_coordinator, clock):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)

        async def incomplete_directory():
            c._client.is_connected = False
            raise TimeoutError("SFR_FILE incomplete")

        c._client.get_sfr_file.side_effect = incomplete_directory
        await refresh_and_settle(c)
        assert c.measurement_available
        assert c.runtime_status["maintenance"]["register_directory_attempted"]
        assert c.runtime_status["maintenance"]["errors"]["register_directory"] == "SFR_FILE incomplete"
        assert c._sfr_file is None
        for _ in range(8):
            clock.now += 61
            c._client.is_connected = True
            c._connection_count += 1
            await refresh_and_settle(c)
            assert c.measurement_available
            assert c._client.is_connected
        assert c._client.get_sfr_file.await_count == 1
        assert c._client.get_firmware_version.await_count == 9
        # A confirmed firmware change permits one new attempt.
        c._client.get_firmware_version.return_value = "4.15"
        c._connection_count += 1
        clock.now += 61
        await refresh_and_settle(c)
        assert c._client.get_sfr_file.await_count == 2
        clock.now += 61
        c._client.is_connected = True
        c._connection_count += 1
        await refresh_and_settle(c)
        assert c._client.get_sfr_file.await_count == 2
        await c.async_shutdown()
    asyncio.run(scenario())


def test_cancelled_directory_does_not_rearm_on_reconnect(make_coordinator):
    async def scenario():
        c = make_coordinator(spectrum_interval=0)
        c._fw_version = "4.15"
        entered = asyncio.Event()

        async def blocked_directory():
            entered.set()
            await asyncio.Event().wait()

        c._client.get_sfr_file.side_effect = blocked_directory
        task = asyncio.create_task(c._fetch_register_directory())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await c._fetch_register_directory()
        assert c._client.get_sfr_file.await_count == 1
        assert c._sfr_attempted
        await c.async_shutdown()
    asyncio.run(scenario())


def test_layout_diagnostics_preserve_numeric_evidence_without_identity(protocol):
    config = '''[DeviceParams]
Serial=SECRET-SERIAL
[DataStructure]
[[GRP_RealTimeData]]
Id=0
[[[CHN_RealTimeFlags]]]
Id=17
DType=4
Sensor=SECRET-SENSOR
[[[CHN_SECRET-NAME]]]
Id=23
DType=2
Expr=1
P1=nan
P2=1e999
[[GRP_DoseCounter]]
Id=4
[[[CHN_DoseCounter]]]
Id=22
DType=8
Unit=" urem| localized name"
[[[CHN_Flags]]]
Id=0
DType=4
[Other]
Id=SECRET-OTHER
'''
    result = protocol.describe_record_layout(config)
    assert result[0]["channels"] == [
        {"name": "CHN_RealTimeFlags", "Id": 17, "DType": 4},
        {"name": "unrecognized", "Id": 23, "DType": 2, "Expr": 1},
    ]
    assert result[1]["channels"][0]["Unit"] == "urem"
    assert "SECRET" not in json.dumps(result)
    assert "localized name" not in json.dumps(result)
    assert len(protocol.describe_record_layout(config * 50)) == 32
