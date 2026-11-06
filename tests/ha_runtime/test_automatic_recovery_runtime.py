"""Timed spectrum recovery publishes through actual HA entities."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_asyncio")

from custom_components.radiacode import coordinator as coordinator_module
from test_coordinator_runtime import runtime  # noqa: F401


@pytest.mark.asyncio
async def test_failed_then_successful_probe_publishes_recovery_state(runtime, monkeypatch):
    hass, c, client, dose, spectrum = runtime
    clock = SimpleNamespace(now=10000.0)
    monkeypatch.setattr(coordinator_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original = client.get_data.return_value

    async def poll():
        client.get_data.return_value = replace(original,
            measurement_time=original.measurement_time + timedelta(seconds=clock.now))
        await c.async_refresh()
        if c._maintenance_task is not None:
            await c._maintenance_task

    await poll()
    acquired = hass.states.get(spectrum.entity_id).attributes["acquired_at"]
    for _ in range(3):
        c._record_spectrum_failure("incomplete spectrum")
    c._publish_maintenance_cache()
    paused = hass.states.get(spectrum.entity_id)
    assert paused.attributes["automatic_paused"]
    assert paused.attributes["automatic_recovery_pending"]
    assert c.spectrum_status["retry_in_seconds"] == 1800
    client.get_spectrum.side_effect = TimeoutError("probe incomplete")
    for failed in (True, False):
        deadline = c._next_spectrum_read
        before = client.get_spectrum.await_count
        for when in range(int(deadline) - 60, int(deadline), 5):
            clock.now = when
            await poll()
            assert client.get_spectrum.await_count == before
        clock.now = deadline
        await poll()
        assert client.get_spectrum.await_count == before + 1
        state = hass.states.get(spectrum.entity_id)
        assert state.state == "1024"
        assert state.attributes["automatic_paused"] is failed
        assert state.attributes["automatic_recovery_pending"] is failed
        assert hass.states.get(dose.entity_id).state == "0.12"
        if failed:
            assert state.attributes["acquired_at"] == acquired
            assert c.spectrum_status["retry_in_seconds"] == 3600
            client.get_spectrum.side_effect = None
        else:
            assert c.spectrum_status["retry_in_seconds"] == 60
            assert c.spectrum_status["consecutive_failures"] == 0
