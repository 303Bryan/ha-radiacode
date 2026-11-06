"""New 1XX statuses published through real HA entities and coordinator."""
import time
import logging
from dataclasses import replace
from datetime import timedelta

import pytest

pytest.importorskip("homeassistant")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

from homeassistant.helpers.entity_platform import EntityPlatform
from custom_components.radiacode.sensor import (
    RadiaCodeQualitySensor, RadiaCodeTemperatureStatusSensor,
    RadiaCodeRadiationAlarmSensor,
)
from custom_components.radiacode.radiacode_ble.protocol import RadiaCodeSettings
from test_coordinator_runtime import runtime


@pytest.mark.asyncio
async def test_quality_statuses_publish_and_expire_without_changing_readings(runtime):
    hass, c, client, dose, _ = runtime
    c._schedule_maintenance = lambda: None
    client.temperature_observed_monotonic = time.monotonic()
    original = client.get_data.return_value
    client.get_data.return_value = replace(original, temperature=25,
        dose_rate_error_percent=31, count_rate_error_percent=20, real_time_flags=2)
    await c.async_refresh()
    platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__),
        domain="sensor", platform_name="radiacode", platform=None,
        scan_interval=timedelta(seconds=30), entity_namespace=None)
    entities = [RadiaCodeQualitySensor(c, c.config_entry, "dose_rate"),
                RadiaCodeQualitySensor(c, c.config_entry, "count_rate"),
                RadiaCodeTemperatureStatusSensor(c, c.config_entry)]
    try:
        for entity, name in zip(entities, ("dose_quality", "count_quality", "temperature_status")):
            entity.entity_id = f"sensor.test_{name}"
            entity.add_to_platform_start(hass, platform, None)
            await entity.add_to_platform_finish()
        assert hass.states.get(dose.entity_id).state == "0.12"
        assert hass.states.get(dose.entity_id).attributes["uncertainty_percent"] == 31
        assert hass.states.get(entities[0].entity_id).state == "reduced"
        assert hass.states.get(entities[1].entity_id).state == "normal"
        assert hass.states.get(entities[2].entity_id).state == "normal"
        # A replay with different error metadata cannot replace accepted quality.
        client.get_data.return_value = replace(original, dose_rate_error_percent=1)
        await c.async_refresh()
        assert hass.states.get(entities[0].entity_id).state == "reduced"
        assert hass.states.get(dose.entity_id).attributes["uncertainty_percent"] == 31
        c._temperature_observed -= 181
        c._status_expiry_handle.cancel()
        c._expire_status()
        assert hass.states.get(entities[1].entity_id).state == "unknown"
        assert hass.states.get(entities[2].entity_id).state == "unknown"
        assert hass.states.get(dose.entity_id).state == "0.12"
        # User disconnect invalidates both live quality and temperature status.
        await c.async_user_disconnect()
        assert hass.states.get(entities[0].entity_id).state == "unavailable"
        assert hass.states.get(entities[2].entity_id).state == "unavailable"
    finally:
        for entity in entities:
            await entity.async_remove(force_remove=True)


@pytest.mark.asyncio
async def test_calculated_count_and_dose_alarm_states_publish(runtime):
    hass, c, client, _, _ = runtime
    c._schedule_maintenance = lambda: None
    original = client.get_data.return_value
    client.get_data.return_value = replace(original, count_rate=60, accumulated_dose=1.2,
        accumulated_dose_time=original.measurement_time)
    c._last_settings = RadiaCodeSettings(cr_alarm_level1=200, cr_alarm_level2=600,
                                         ds_alarm_level1=40, ds_alarm_level2=120)
    await c.async_refresh()
    platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__),
        domain="sensor", platform_name="radiacode", platform=None,
        scan_interval=timedelta(seconds=30), entity_namespace=None)
    entities = [RadiaCodeRadiationAlarmSensor(c, c.config_entry, kind)
                for kind in ("count_rate", "accumulated_dose")]
    try:
        for entity, name in zip(entities, ("count_alarm", "dose_alarm")):
            entity.entity_id = f"sensor.test_{name}"
            entity.add_to_platform_start(hass, platform, None)
            await entity.add_to_platform_finish()
            state = hass.states.get(entity.entity_id)
            assert state.state == "L2 Alarm"
            assert state.attributes["source"] == "calculated"
        c._dose_observed -= 1680
        c._publish_maintenance_cache()
        assert hass.states.get(entities[1].entity_id).state == "L2 Alarm"
        assert hass.states.get(entities[1].entity_id).attributes["measurement_age_seconds"] >= 1680
        c._dose_observed -= 1921
        c._publish_maintenance_cache()
        assert hass.states.get(entities[1].entity_id).state == "L2 Alarm"
        assert hass.states.get(entities[1].entity_id).attributes["measurement_age_seconds"] >= 3600
        assert hass.states.get(entities[0].entity_id).state == "L2 Alarm"
        # Expiry must publish to both alarms, and fresh rates alone must not
        # revive the old dose total after continuity has been lost.
        c._last_fresh_monotonic -= 61
        c._cancel_measurement_expiry()
        c._expire_measurement()
        assert hass.states.get(entities[1].entity_id).state == "unavailable"
        client.get_data.return_value = replace(original, count_rate=60,
            measurement_time=original.measurement_time + timedelta(seconds=65))
        await c.async_refresh()
        assert hass.states.get(entities[0].entity_id).state == "L2 Alarm"
        assert hass.states.get(entities[1].entity_id).state == "unavailable"
        client.get_data.return_value = replace(client.get_data.return_value,
            accumulated_dose=1.3,
            accumulated_dose_time=original.measurement_time + timedelta(seconds=70))
        await c.async_refresh()
        assert hass.states.get(entities[1].entity_id).state == "L2 Alarm"
    finally:
        for entity in entities:
            await entity.async_remove(force_remove=True)


@pytest.mark.asyncio
async def test_dose_observation_never_extends_rates_and_requires_live_measurements(runtime):
    from unittest.mock import AsyncMock
    from custom_components.radiacode.sensor import RadiaCodeSensor, SENSOR_DESCRIPTIONS
    hass, c, client, dose_rate, _ = runtime
    c._schedule_maintenance = lambda: None
    c._last_settings = RadiaCodeSettings(ds_alarm_level1=40, ds_alarm_level2=120)
    await c.async_refresh()
    lease = c._last_fresh_monotonic
    platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__),
        domain="sensor", platform_name="radiacode", platform=None,
        scan_interval=timedelta(seconds=30), entity_namespace=None)
    entities = [RadiaCodeRadiationAlarmSensor(c, c.config_entry, "accumulated_dose"),
        RadiaCodeSensor(c, c.config_entry, next(d for d in SENSOR_DESCRIPTIONS if d.key == "accumulated_dose"))]
    try:
        for entity, name in zip(entities, ("dose_alarm_register", "accumulated_dose_register")):
            entity.entity_id = f"sensor.test_{name}"
            entity.add_to_platform_start(hass, platform, None)
            await entity.add_to_platform_finish()
            assert hass.states.get(entity.entity_id).state == "unavailable"
        client.get_accumulated_dose = AsyncMock(return_value=1.2)
        await c._refresh_accumulated_dose()
        c._publish_maintenance_cache()
        assert hass.states.get(entities[0].entity_id).state == "L2 Alarm"
        assert hass.states.get(entities[1].entity_id).state == "1.2"
        assert hass.states.get(entities[0].entity_id).attributes["measurement_source"] == "dose_register"
        assert c._last_fresh_monotonic == lease
        # A dose observation cannot hide a real live-stream outage.
        c._last_fresh_monotonic -= 61
        c._cancel_measurement_expiry()
        c._expire_measurement()
        assert hass.states.get(dose_rate.entity_id).state == "unavailable"
        assert hass.states.get(entities[0].entity_id).state == "unavailable"
        c._dose_observed -= 1801
        c._publish_maintenance_cache()
        for entity in entities:
            assert hass.states.get(entity.entity_id).state == "unavailable"
        await c._refresh_accumulated_dose()
        c._publish_maintenance_cache()
        assert hass.states.get(entities[0].entity_id).state == "unavailable"
        assert hass.states.get(dose_rate.entity_id).state == "unavailable"
    finally:
        for entity in entities:
            await entity.async_remove(force_remove=True)
