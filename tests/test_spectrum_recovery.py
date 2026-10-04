"""Stop optional failed bulk reads from repeatedly interrupting radiation."""

import asyncio

from test_coordinator import (  # noqa: F401
    clock, coordinator_module, make_coordinator, refresh_and_settle,
)


def test_repeated_spectrum_failures_pause_automatic_reads(make_coordinator, clock):
    async def scenario():
        coordinator = make_coordinator()
        cached = (await refresh_and_settle(coordinator)).spectrum
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
            assert coordinator.last_update_success
        assert status["automatic_paused"]
        assert status["last_error"] == "missing notification packets"

        for when in (5560, 9160, 100000):
            clock.now = when
            await refresh_and_settle(coordinator)
            assert client.get_spectrum.await_count == 4
            assert coordinator.data.spectrum is cached
            assert coordinator.data.sensors.count_rate == 3.0
        assert coordinator.connection_count == 3
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_only_successful_current_spectrum_read_resumes_automatic_reads(
    make_coordinator, clock, protocol,
):
    async def scenario():
        coordinator = make_coordinator()
        await refresh_and_settle(coordinator)
        client = coordinator._client
        for _ in range(3):
            coordinator._record_spectrum_failure("incomplete spectrum")
        assert coordinator.spectrum_status["automatic_paused"]

        # An accumulated histogram does not validate the current-spectrum path.
        await coordinator.async_get_spectrum(accumulated=True)
        assert coordinator.spectrum_status["automatic_paused"]

        client.get_spectrum.side_effect = TimeoutError("still incomplete")
        try:
            await coordinator.async_get_spectrum()
        except Exception as err:
            assert "still incomplete" in str(err)
        else:
            raise AssertionError("Failed manual spectrum must not resume polling")
        assert coordinator.spectrum_status["automatic_paused"]

        complete = protocol.Spectrum(120, 0, 3, 0, [2] * 1024)
        client.get_spectrum.side_effect = None
        client.get_spectrum.return_value = complete
        assert await coordinator.async_get_spectrum() is complete
        assert coordinator.data.spectrum is complete
        status = coordinator.spectrum_status
        assert not status["automatic_paused"]
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
        await coordinator.async_shutdown()

    asyncio.run(scenario())
