"""Bounded sensor availability through actual failed and gated acquisition."""

import asyncio
from unittest.mock import AsyncMock

from test_coordinator import (  # noqa: F401
    clock, coordinator_module, make_coordinator, refresh_and_settle,
)


def test_transport_failure_keeps_recent_sample_without_renewing_lease(
    make_coordinator, coordinator_module, clock,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        assert not coordinator.measurement_available
        await refresh_and_settle(coordinator)
        timer = coordinator._measurement_expiry_handle
        clock.now = 1005
        coordinator._poll_with_retry = AsyncMock(
            side_effect=coordinator_module.UpdateFailed("reconnect failed")
        )
        await coordinator.async_refresh()
        assert not coordinator.last_update_success
        assert coordinator.measurement_available
        assert coordinator._last_fresh_monotonic == 1000
        assert coordinator._measurement_expiry_handle is timer
        assert coordinator.runtime_status["freshness"]["using_cached_measurement"]
        assert coordinator.last_error == "reconnect failed"
        clock.now = 1060
        assert not coordinator.measurement_available
        updates = coordinator.listener_updates
        coordinator._cancel_measurement_expiry()
        coordinator._expire_measurement()
        assert coordinator.listener_updates == updates + 1
        await coordinator.async_shutdown()
    asyncio.run(scenario())


def test_only_fresh_pair_replaces_expiry_timer(make_coordinator, clock):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        await refresh_and_settle(coordinator)
        first_timer = coordinator._measurement_expiry_handle
        clock.now += 5
        await refresh_and_settle(coordinator)
        assert first_timer.cancelled()
        renewed = coordinator._measurement_expiry_handle
        coordinator._client.get_data.side_effect = None
        coordinator._client.get_data.return_value = coordinator.data.sensors
        clock.now += 5
        await refresh_and_settle(coordinator)
        assert coordinator._measurement_expiry_handle is renewed
        assert coordinator._last_fresh_monotonic == 1005
        await coordinator.async_shutdown()
        assert renewed.cancelled()
        assert coordinator._measurement_expiry_handle is None
    asyncio.run(scenario())


def test_expiry_notifies_while_primary_refresh_is_blocked(make_coordinator, clock):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        coordinator._freshness_grace = 0.03
        await refresh_and_settle(coordinator)
        entered, release = asyncio.Event(), asyncio.Event()
        sample = coordinator.data.sensors
        async def blocked_poll(_):
            entered.set()
            await release.wait()
            return sample
        coordinator._poll_with_retry = AsyncMock(side_effect=blocked_poll)
        pending = asyncio.create_task(coordinator.async_refresh())
        await entered.wait()
        updates = coordinator.listener_updates
        clock.now += 0.04
        await asyncio.sleep(0.06)
        assert not pending.done()
        assert not coordinator.measurement_available
        assert coordinator.listener_updates == updates + 1
        assert coordinator._measurement_expiry_handle is None
        release.set()
        await pending
        await coordinator.async_shutdown()
    asyncio.run(scenario())


def test_user_off_invalidates_before_slow_disconnect(make_coordinator):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        await refresh_and_settle(coordinator)
        timer = coordinator._measurement_expiry_handle
        entered, release = asyncio.Event(), asyncio.Event()
        async def disconnect():
            entered.set()
            await release.wait()
            coordinator._client.is_connected = False
        coordinator._client.disconnect.side_effect = disconnect
        updates = coordinator.listener_updates
        pending = asyncio.create_task(coordinator.async_user_disconnect())
        await entered.wait()
        assert not pending.done()
        assert not coordinator.measurement_available
        assert timer.cancelled()
        assert coordinator._measurement_expiry_handle is None
        assert coordinator.listener_updates == updates + 1
        release.set()
        await pending
        assert coordinator.listener_updates == updates + 2
        await coordinator.async_shutdown()
    asyncio.run(scenario())


def test_failed_user_reconnect_rearms_remaining_lease(
    make_coordinator, coordinator_module, clock,
):
    async def scenario():
        coordinator = make_coordinator(spectrum_interval=0)
        await refresh_and_settle(coordinator)
        await coordinator.async_user_disconnect()
        assert coordinator._measurement_expiry_handle is None
        assert not coordinator.measurement_available
        clock.now = 1005
        coordinator._poll_with_retry = AsyncMock(
            side_effect=coordinator_module.UpdateFailed("reconnect failed")
        )
        await coordinator.async_user_reconnect()
        assert coordinator._measurement_expiry_handle is not None
        assert coordinator.measurement_available
        assert not coordinator.last_update_success
        assert coordinator._last_fresh_monotonic == 1000
        clock.now = 1060
        coordinator._cancel_measurement_expiry()
        coordinator._expire_measurement()
        assert not coordinator.measurement_available
        await coordinator.async_shutdown()
    asyncio.run(scenario())
