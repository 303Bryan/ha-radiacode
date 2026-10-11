"""Stop optional failed bulk reads from repeatedly interrupting radiation."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from test_coordinator import (  # noqa: F401
    clock, coordinator_module, make_coordinator, refresh_and_settle,
)


def test_repeated_spectrum_failures_pause_automatic_reads(make_coordinator, clock):
    async def scenario():
        coordinator = make_coordinator()
        cached = (await refresh_and_settle(coordinator)).spectrum
        acquired_at = coordinator.spectrum_status["acquired_at"]
        assert datetime.fromisoformat(acquired_at).tzinfo == timezone.utc
        client = coordinator._client
        async def failed_bulk_transfer():
            # Incomplete framing retires the real transport. Recovery of the
            # primary stream must not reopen this optional failure loop.
            client.is_connected = False
            raise TimeoutError("missing notification packets")
        client.get_spectrum.side_effect = failed_bulk_transfer

        for when, failures, delay in ((1060, 1, 300), (1360, 2, 600), (1960, 3, None)):
            clock.now = when
            await refresh_and_settle(coordinator)
            status = coordinator.spectrum_status
            assert status["consecutive_failures"] == failures
            assert status["retry_in_seconds"] == delay
            assert coordinator.data.spectrum is cached
            assert coordinator.spectrum_status["acquired_at"] == acquired_at
            assert coordinator.last_update_success
        assert status["automatic_paused"]
        assert status["last_error"] == "missing notification packets"

        for when in (5560, 9160, 100000):
            clock.now = when
            await refresh_and_settle(coordinator)
            assert client.get_spectrum.await_count == 4
            assert coordinator.data.spectrum is cached
            assert coordinator.spectrum_status["acquired_at"] == acquired_at
            assert coordinator.data.sensors.count_rate == 3.0
        assert coordinator.connection_count == 3
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_only_successful_current_spectrum_read_resumes_automatic_reads(
    make_coordinator, clock, protocol, utc_clock,
):
    async def scenario():
        coordinator = make_coordinator()
        await refresh_and_settle(coordinator)
        acquired_at = coordinator.spectrum_status["acquired_at"]
        cached = coordinator.data.spectrum
        utc_clock.now += timedelta(minutes=5)
        client = coordinator._client
        for _ in range(3):
            coordinator._record_spectrum_failure("incomplete spectrum")
        assert coordinator.spectrum_status["automatic_paused"]

        # An accumulated histogram does not validate the current-spectrum path.
        client.get_spectrum.return_value = protocol.Spectrum(9000, 0, 3, 0, [9] * 1024)
        await coordinator.async_get_spectrum(accumulated=True)
        assert coordinator.spectrum_status["automatic_paused"]
        assert coordinator.spectrum_status["acquired_at"] == acquired_at
        assert coordinator.data.spectrum is cached

        client.get_spectrum.side_effect = TimeoutError("still incomplete")
        try:
            await coordinator.async_get_spectrum()
        except Exception as err:
            assert "still incomplete" in str(err)
        else:
            raise AssertionError("Failed manual spectrum must not resume polling")
        assert coordinator.spectrum_status["automatic_paused"]
        assert coordinator.spectrum_status["acquired_at"] == acquired_at

        complete = protocol.Spectrum(120, 0, 3, 0, [2] * 1024)
        client.get_spectrum.side_effect = None
        client.get_spectrum.return_value = complete
        assert await coordinator.async_get_spectrum() is complete
        assert coordinator.data.spectrum is complete
        status = coordinator.spectrum_status
        assert not status["automatic_paused"]
        assert status["acquired_at"] == utc_clock.now.isoformat()
        assert status["acquired_at"] != acquired_at
        assert status["consecutive_failures"] == 0
        assert status["last_error"] is None
        assert status["retry_in_seconds"] == 60
        before = client.get_spectrum.await_count
        clock.now += 60
        await refresh_and_settle(coordinator)
        assert client.get_spectrum.await_count == before + 1
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_disabled_spectrum_remains_disabled_after_manual_success(make_coordinator):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        await refresh_and_settle(coordinator)
        for _ in range(3):
            coordinator._record_spectrum_failure("incomplete spectrum")
        await coordinator.async_get_spectrum()
        assert not coordinator.spectrum_status["automatic_paused"]
        assert coordinator.spectrum_status["retry_in_seconds"] is None
        await refresh_and_settle(coordinator)
        coordinator._client.get_spectrum.assert_awaited_once()
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_explicit_spectrum_reset_restarts_bounded_attempts(make_coordinator):
    async def scenario():
        coordinator = make_coordinator()
        await refresh_and_settle(coordinator)
        acquired_at = coordinator.spectrum_status["acquired_at"]
        for _ in range(3):
            coordinator._record_spectrum_failure("incomplete spectrum")
        assert coordinator.spectrum_status["automatic_paused"]
        client = coordinator._client
        client.reset_spectrum.return_value = False
        try:
            await coordinator.async_reset_spectrum()
        except Exception as err:
            assert "rejected" in str(err)
        else:
            raise AssertionError("Rejected reset must not resume acquisition")
        assert coordinator.spectrum_status["automatic_paused"]
        assert coordinator.spectrum_status["acquired_at"] == acquired_at
        client.reset_spectrum.return_value = True
        client.get_spectrum.side_effect = TimeoutError("still incomplete")
        await coordinator.async_reset_spectrum()
        # Reset starts the one managed worker; its first failed read begins a
        # new series rather than immediately restoring the old paused state.
        worker = coordinator._maintenance_task
        if worker is not None:
            await worker
        status = coordinator.spectrum_status
        assert not status["automatic_paused"]
        assert status["consecutive_failures"] == 1
        assert status["retry_in_seconds"] == 300
        assert coordinator.data.spectrum is None
        assert status["acquired_at"] is None
        await coordinator.async_shutdown()

    asyncio.run(scenario())


@pytest.fixture
def utc_clock(monkeypatch, coordinator_module):
    """Control host UTC completion time separately from the monotonic timer."""
    result = SimpleNamespace(now=datetime(2026, 10, 11, 2, 20, 38, tzinfo=timezone.utc))

    class HostDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return result.now

    monkeypatch.setattr(coordinator_module, "datetime", HostDatetime)
    return result


def test_acquisition_time_is_host_utc_current_read_completion_only(
    make_coordinator, utc_clock, protocol,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        assert coordinator.spectrum_entity_attributes == {
            "acquired_at": None, "automatic_paused": False,
        }
        assert coordinator.spectrum_status["acquired_at"] is None
        await refresh_and_settle(coordinator)
        assert coordinator.spectrum_status["acquired_at"] is None
        started_at = utc_clock.now

        async def completed_current_read(accumulated=False):
            assert not accumulated
            utc_clock.now += timedelta(seconds=9)
            # Device accumulation duration cannot determine acquisition time.
            return protocol.Spectrum(9999999, 0, 3, 0, [1] * 1024)

        coordinator._client.get_spectrum.side_effect = completed_current_read
        await coordinator.async_get_spectrum()
        assert coordinator._last_spectrum_acquired_at == started_at + timedelta(seconds=9)
        assert coordinator.spectrum_entity_attributes == {
            "acquired_at": utc_clock.now.isoformat(), "automatic_paused": False,
        }
        assert coordinator.spectrum_status["acquired_at"] == utc_clock.now.isoformat()
        acquired_at = coordinator.spectrum_status["acquired_at"]
        utc_clock.now += timedelta(hours=2)
        await refresh_and_settle(coordinator)
        assert coordinator.spectrum_status["acquired_at"] == acquired_at
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_manual_current_failure_publishes_pause_after_transport_disconnect(
    make_coordinator, coordinator_module,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        await refresh_and_settle(coordinator)
        for _ in range(2):
            coordinator._record_spectrum_failure("incomplete spectrum")
        before = coordinator.listener_updates
        fresh_before = coordinator._last_fresh_monotonic
        coordinator.last_update_success = False

        async def failed_current_read(accumulated=False):
            assert not accumulated
            coordinator._client.is_connected = False
            raise TimeoutError("transport retired")

        coordinator._client.get_spectrum.side_effect = failed_current_read
        with pytest.raises(coordinator_module.UpdateFailed, match="transport retired"):
            await coordinator.async_get_spectrum()
        assert not coordinator._client.is_connected
        assert coordinator.listener_updates == before + 1
        assert coordinator.spectrum_entity_attributes == {
            "acquired_at": None, "automatic_paused": True,
        }
        assert coordinator.data.spectrum is None
        assert coordinator._last_fresh_monotonic == fresh_before
        assert not coordinator.last_update_success
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_confirmed_reset_clears_time_but_failed_reset_preserves_it(
    make_coordinator, coordinator_module, utc_clock,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        await refresh_and_settle(coordinator)
        await coordinator.async_get_spectrum()
        cached = coordinator.data.spectrum
        acquired_at = coordinator.spectrum_status["acquired_at"]
        for _ in range(3):
            coordinator._record_spectrum_failure("incomplete spectrum")
        utc_clock.now += timedelta(minutes=2)
        client = coordinator._client
        client.reset_spectrum.side_effect = TimeoutError("reset response missing")
        with pytest.raises(coordinator_module.UpdateFailed, match="reset response missing"):
            await coordinator.async_reset_spectrum()
        assert coordinator.spectrum_status["acquired_at"] == acquired_at
        assert coordinator.data.spectrum is cached
        assert coordinator.spectrum_status["automatic_paused"]

        client.reset_spectrum.side_effect = None
        await coordinator.async_reset_spectrum()
        assert coordinator.data.spectrum is None
        assert coordinator._last_spectrum_acquired_at is None
        assert coordinator.spectrum_entity_attributes == {
            "acquired_at": None, "automatic_paused": False,
        }
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_accumulated_failure_does_not_change_or_publish_current_status(
    make_coordinator, coordinator_module, utc_clock,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        await refresh_and_settle(coordinator)
        await coordinator.async_get_spectrum()
        for _ in range(3):
            coordinator._record_spectrum_failure("incomplete current spectrum")
        metadata = coordinator.spectrum_entity_attributes
        cached = coordinator.data.spectrum
        before = coordinator.listener_updates
        utc_clock.now += timedelta(minutes=2)
        coordinator._client.get_spectrum.side_effect = TimeoutError("accumulated incomplete")
        with pytest.raises(coordinator_module.UpdateFailed, match="accumulated incomplete"):
            await coordinator.async_get_spectrum(accumulated=True)
        assert coordinator.spectrum_entity_attributes == metadata
        assert coordinator.data.spectrum is cached
        assert coordinator.listener_updates == before
        await coordinator.async_shutdown()

    asyncio.run(scenario())
