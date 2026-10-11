"""Control states through the actual Home Assistant coordinator/entity lifecycle."""

import datetime as dt
import logging
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.radiacode.button import BUTTON_DESCRIPTIONS, RadiaCodeButton
from custom_components.radiacode.coordinator import RadiaCodeCoordinator
from custom_components.radiacode.number import NUMBER_DESCRIPTIONS, RadiaCodeNumber
from custom_components.radiacode.radiacode_ble.protocol import RadiaCodeData, RadiaCodeSettings
from custom_components.radiacode.select import SELECT_DESCRIPTIONS, RadiaCodeSelect
from custom_components.radiacode.sensor import SENSOR_DESCRIPTIONS, RadiaCodeSensor
from custom_components.radiacode.switch import (
    SWITCH_DESCRIPTIONS,
    RadiaCodeConnectionSwitch,
    RadiaCodeSwitch,
)


@pytest_asyncio.fixture
async def control_runtime(tmp_path):
    """Use real HA publication with a controlled, already-connected BLE client."""
    hass = HomeAssistant(str(tmp_path))
    entry = ConfigEntry(
        data={"address": "AA:BB:CC:DD:EE:FF", "name": "Test detector"},
        options={"poll_interval": 5, "spectrum_interval": 0},
        domain="radiacode", title="Test detector", version=1, minor_version=1,
        source="user", unique_id="AA:BB:CC:DD:EE:FF",
        discovery_keys=MappingProxyType({}), subentries_data=None,
    )
    coordinator = RadiaCodeCoordinator(hass, entry)
    client = SimpleNamespace(
        is_connected=True,
        get_data=AsyncMock(return_value=RadiaCodeData(
            dose_rate=0.12, count_rate=12.0, accumulated_dose=None,
            battery=None, temperature=None,
            measurement_time=dt.datetime(2026, 1, 1, 12),
            measurement_type="RealTimeData", measurement_flags=0x0040,
        )),
        disconnect=AsyncMock(),
    )
    coordinator._client = client
    # No maintenance is due; each test explicitly publishes setting changes.
    coordinator._serial_number = "TEST-DEVICE"
    coordinator._next_settings_read = float("inf")
    coordinator._next_diagnostics_read = float("inf")
    coordinator._next_temperature_read = float("inf")
    coordinator._last_settings = RadiaCodeSettings(
        display_brightness=3, display_direction=0, sound_on=False,
    )
    entities = {
        "number": RadiaCodeNumber(coordinator, entry, NUMBER_DESCRIPTIONS[0]),
        "select": RadiaCodeSelect(coordinator, entry, SELECT_DESCRIPTIONS[0]),
        "switch": RadiaCodeSwitch(coordinator, entry, SWITCH_DESCRIPTIONS[0]),
        "dose_reset": RadiaCodeButton(coordinator, entry, BUTTON_DESCRIPTIONS[0]),
        "spectrum_reset": RadiaCodeButton(coordinator, entry, BUTTON_DESCRIPTIONS[1]),
        "connection": RadiaCodeConnectionSwitch(coordinator, entry),
        "dose": RadiaCodeSensor(coordinator, entry, SENSOR_DESCRIPTIONS[0]),
    }
    domains = {
        "number": "number", "select": "select", "switch": "switch",
        "dose_reset": "button", "spectrum_reset": "button",
        "connection": "switch", "dose": "sensor",
    }
    platforms = {
        domain: EntityPlatform(
            hass=hass, logger=logging.getLogger(__name__), domain=domain,
            platform_name="radiacode", platform=None,
            scan_interval=timedelta(seconds=30), entity_namespace=None,
        )
        for domain in set(domains.values())
    }
    for name, entity in entities.items():
        entity.entity_id = f"{domains[name]}.test_detector_{name}"
        entity.add_to_platform_start(hass, platforms[domains[name]], None)
        await entity.add_to_platform_finish()
    try:
        yield hass, coordinator, client, entities
    finally:
        await coordinator.async_shutdown()
        for entity in entities.values():
            await entity.async_remove(force_remove=True)
        await hass.async_stop(force=True)


def assert_known_control_states(hass, entities):
    """Known cached settings and reset actions remain represented in HA."""
    assert hass.states.get(entities["number"].entity_id).state == "3.0"
    assert hass.states.get(entities["select"].entity_id).state == "Auto"
    assert hass.states.get(entities["switch"].entity_id).state == "off"
    for name in ("dose_reset", "spectrum_reset"):
        assert hass.states.get(entities[name].entity_id).state == "unknown"
        assert entities[name].available


@pytest.mark.asyncio
async def test_connected_controls_survive_primary_failure_and_measurement_expiry(control_runtime):
    hass, coordinator, _, entities = control_runtime
    await coordinator.async_refresh()
    assert_known_control_states(hass, entities)
    coordinator.async_set_update_error(UpdateFailed("radiation stream stale"))
    assert not coordinator.last_update_success
    assert_known_control_states(hass, entities)
    # Expire radiation through the same timer callback used during blocked BLE work.
    coordinator._last_fresh_monotonic -= coordinator._freshness_grace
    coordinator._cancel_measurement_expiry()
    coordinator._expire_measurement()
    assert hass.states.get(entities["dose"].entity_id).state == "unavailable"
    assert_known_control_states(hass, entities)
    # Optional settings still publish while primary acquisition remains failed.
    coordinator._last_settings = RadiaCodeSettings(
        display_brightness=0, display_direction=2, sound_on=True,
    )
    coordinator._publish_maintenance_cache()
    assert not coordinator.last_update_success
    assert hass.states.get(entities["number"].entity_id).state == "0.0"
    assert hass.states.get(entities["select"].entity_id).state == "Left"
    assert hass.states.get(entities["switch"].entity_id).state == "on"
    assert hass.states.get(entities["dose"].entity_id).state == "unavailable"


@pytest.mark.asyncio
async def test_unknown_settings_wait_for_cache_but_resets_need_only_live_ble(control_runtime):
    hass, coordinator, _, entities = control_runtime
    coordinator._last_settings = RadiaCodeSettings()
    await coordinator.async_refresh()
    for name in ("number", "select", "switch"):
        assert hass.states.get(entities[name].entity_id).state == "unavailable"
    for name in ("dose_reset", "spectrum_reset"):
        assert entities[name].available
    coordinator._last_settings = RadiaCodeSettings(
        display_brightness=0, display_direction=0, sound_on=False,
    )
    coordinator._publish_maintenance_cache()
    assert hass.states.get(entities["number"].entity_id).state == "0.0"
    assert hass.states.get(entities["select"].entity_id).state == "Auto"
    assert hass.states.get(entities["switch"].entity_id).state == "off"
    # A register value without a supported option must not enable its dropdown.
    coordinator._last_settings.display_direction = 99
    coordinator._publish_maintenance_cache()
    assert hass.states.get(entities["select"].entity_id).state == "unavailable"


@pytest.mark.asyncio
async def test_known_settings_publish_before_first_radiation_sample(control_runtime):
    hass, coordinator, client, entities = control_runtime
    client.get_data.return_value = RadiaCodeData(
        dose_rate=None, count_rate=None, accumulated_dose=None,
        battery=None, temperature=None,
    )
    await coordinator.async_refresh()
    assert coordinator.data is None
    assert not coordinator.last_update_success
    assert hass.states.get(entities["dose"].entity_id).state == "unavailable"
    assert_known_control_states(hass, entities)
    # A successful optional setting read must publish even without primary data.
    coordinator._last_settings = RadiaCodeSettings(
        display_brightness=0, display_direction=2, sound_on=True,
    )
    coordinator._publish_maintenance_cache()
    assert coordinator.data is None
    assert not coordinator.last_update_success
    assert hass.states.get(entities["number"].entity_id).state == "0.0"
    assert hass.states.get(entities["select"].entity_id).state == "Left"
    assert hass.states.get(entities["switch"].entity_id).state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ("ble_lost", "user_disabled", "shutdown"))
async def test_controls_require_active_allowed_ble(control_runtime, reason):
    hass, coordinator, client, entities = control_runtime
    await coordinator.async_refresh()
    assert_known_control_states(hass, entities)
    if reason == "ble_lost":
        client.is_connected = False
        coordinator.async_update_listeners()
    elif reason == "user_disabled":
        # Availability must fall immediately even if transport cleanup is pending.
        await coordinator.async_user_disconnect()
    else:
        await coordinator.async_shutdown()
        coordinator.async_update_listeners()
    for name in ("number", "select", "switch", "dose_reset", "spectrum_reset"):
        assert not entities[name].available
        assert hass.states.get(entities[name].entity_id).state == "unavailable"
    # The integration's connection toggle remains usable for reconnection.
    assert entities["connection"].available
    assert hass.states.get(entities["connection"].entity_id).state != "unavailable"
