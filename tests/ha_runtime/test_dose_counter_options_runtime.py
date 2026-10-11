"""Dose-counter semantics and write options through the real HA APIs."""

import datetime as dt
import logging
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.radiacode import _async_update_listener
from custom_components.radiacode.config_flow import RadiaCodeOptionsFlow
from custom_components.radiacode.coordinator import RadiaCodeCoordinator
from custom_components.radiacode.radiacode_ble.protocol import RadiaCodeData
from custom_components.radiacode.sensor import SENSOR_DESCRIPTIONS, RadiaCodeSensor


def make_entry(**options):
    return ConfigEntry(
        data={"address": "AA:BB:CC:DD:EE:FF", "name": "Test detector"},
        options={"poll_interval": 5, "spectrum_interval": 0, **options},
        domain="radiacode", title="Test detector", version=1, minor_version=1,
        source="user", unique_id="AA:BB:CC:DD:EE:FF",
        discovery_keys=MappingProxyType({}), subentries_data=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", (False, True))
async def test_write_option_form_preserves_choice_and_validates_boolean(tmp_path, enabled):
    hass = HomeAssistant(str(tmp_path))
    entry = make_entry(**({"acknowledged_writes": True} if enabled else {}))
    flow = RadiaCodeOptionsFlow(entry)
    flow.hass = hass
    flow.flow_id = "test-options"
    try:
        result = await flow.async_step_init()
        submitted = result["data_schema"]({"poll_interval": 5, "spectrum_interval": 0})
        assert submitted["acknowledged_writes"] is enabled
        with pytest.raises(vol.Invalid):
            result["data_schema"]({
                "poll_interval": 5, "spectrum_interval": 0,
                "acknowledged_writes": "true",
            })
        saved = await flow.async_step_init(submitted)
        assert saved["data"]["acknowledged_writes"] is enabled
        with patch("custom_components.radiacode.coordinator.RadiaCodeBLEClient") as factory:
            factory.return_value = SimpleNamespace(disconnect=AsyncMock())
            coordinator = RadiaCodeCoordinator(hass, entry)
            factory.assert_called_once_with(acknowledged_writes=enabled)
            await coordinator.async_shutdown()
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_write_option_uses_existing_reload_listener():
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=AsyncMock()))
    entry = make_entry(acknowledged_writes=True)
    await _async_update_listener(hass, entry)
    hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_dose_counter_publishes_separately_and_survives_dose_reset(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = make_entry()
    coordinator = RadiaCodeCoordinator(hass, entry)
    client = SimpleNamespace(
        is_connected=True,
        get_data=AsyncMock(return_value=RadiaCodeData(
            dose_rate=0.12, count_rate=3.0, accumulated_dose=2.0,
            battery=None, temperature=None, dose_counter=1060.09,
            measurement_time=dt.datetime(2026, 1, 1, 12),
            measurement_type="RealTimeData",
        )),
        write_vsfr=AsyncMock(return_value=True), disconnect=AsyncMock(),
    )
    coordinator._client = client
    coordinator._serial_number = "TEST-DEVICE"
    for name in ("settings", "diagnostics", "temperature"):
        setattr(coordinator, "_next_" + name + "_read", float("inf"))
    platform = EntityPlatform(
        hass=hass, logger=logging.getLogger(__name__), domain="sensor",
        platform_name="radiacode", platform=None,
        scan_interval=timedelta(seconds=30), entity_namespace=None,
    )
    entities = {}
    for key in ("dose_counter", "accumulated_dose"):
        description = next(item for item in SENSOR_DESCRIPTIONS if item.key == key)
        entity = RadiaCodeSensor(coordinator, entry, description)
        entity.entity_id = "sensor.test_detector_" + key
        entity.add_to_platform_start(hass, platform, None)
        await entity.add_to_platform_finish()
        entities[key] = entity
    try:
        await coordinator.async_refresh()
        state = hass.states.get(entities["dose_counter"].entity_id)
        assert state.state == "1060.09"
        assert state.attributes["unit_of_measurement"] == "µSv"
        assert state.attributes["state_class"] == "total"
        assert "last_reset" not in state.attributes
        assert hass.states.get(entities["accumulated_dose"].entity_id).state == "2.0"
        client.get_data.return_value = RadiaCodeData(
            dose_rate=None, count_rate=None, accumulated_dose=None,
            battery=None, temperature=None, dose_counter=1060.19,
        )
        lease = coordinator._last_fresh_monotonic
        await coordinator.async_refresh()
        assert coordinator._last_fresh_monotonic == lease
        assert hass.states.get(entities["dose_counter"].entity_id).state == "1060.19"
        assert hass.states.get(entities["accumulated_dose"].entity_id).state == "2.0"
        client.get_data.return_value.dose_counter = None
        await coordinator.async_reset_dose()
        assert hass.states.get(entities["accumulated_dose"].entity_id).state == "0.0"
        assert hass.states.get(entities["dose_counter"].entity_id).state == "1060.19"
        coordinator._last_fresh_monotonic -= coordinator._freshness_grace
        coordinator._cancel_measurement_expiry()
        coordinator._expire_measurement()
        assert hass.states.get(entities["dose_counter"].entity_id).state == "unavailable"
    finally:
        await coordinator.async_shutdown()
        for entity in entities.values():
            await entity.async_remove(force_remove=True)
        await hass.async_stop(force=True)
