"""Isolated entity tests without installing Home Assistant or using BLE.

The Home Assistant boundary is stubbed; these tests exercise the real
sensor module's cache and coordinator listener, not HA's recorder/frontend.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def sensor_module(monkeypatch, protocol):
    """Load sensor.py with minimal, local Home Assistant API stand-ins."""

    class Entity:
        @property
        def native_value(self):
            return self._attr_native_value

        @property
        def extra_state_attributes(self):
            return self._attr_extra_state_attributes

        async def async_added_to_hass(self):
            pass

        def async_on_remove(self, callback):
            pass

        def async_write_ha_state(self):
            self.writes.append(
                (self.available, self.native_value, self.extra_state_attributes)
            )

    class CoordinatorEntity(Entity):
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator
            self.writes = []

        @property
        def available(self):
            return self.coordinator.last_update_success

        async def async_added_to_hass(self):
            await super().async_added_to_hass()
            self.async_on_remove(
                self.coordinator.async_add_listener(self._handle_coordinator_update)
            )

    def module(name, **attributes):
        result = ModuleType(name)
        result.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    module("homeassistant", __path__=[])
    module("homeassistant.components", __path__=[])
    module("homeassistant.components.bluetooth")
    module(
        "homeassistant.components.sensor",
        SensorDeviceClass=SimpleNamespace(
            BATTERY="battery", TEMPERATURE="temperature", VOLTAGE="voltage",
            SIGNAL_STRENGTH="signal_strength", ENUM="enum",
        ),
        SensorStateClass=SimpleNamespace(
            MEASUREMENT="measurement", TOTAL_INCREASING="total_increasing",
        ),
        SensorEntity=Entity,
        SensorEntityDescription=SimpleNamespace,
    )
    module("homeassistant.config_entries", ConfigEntry=SimpleNamespace)
    module(
        "homeassistant.const",
        PERCENTAGE="%", SIGNAL_STRENGTH_DECIBELS_MILLIWATT="dBm",
        UnitOfElectricPotential=SimpleNamespace(MILLIVOLT="mV"),
        UnitOfTemperature=SimpleNamespace(CELSIUS="°C"),
    )
    module("homeassistant.core", HomeAssistant=object, callback=lambda fn: fn)
    module("homeassistant.helpers", __path__=[])
    module(
        "homeassistant.helpers.entity",
        EntityCategory=SimpleNamespace(DIAGNOSTIC="diagnostic"),
    )
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module("homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity)
    module(
        "homeassistant.helpers.device_registry",
        CONNECTION_BLUETOOTH="bluetooth", DeviceInfo=dict,
    )

    package = "radiacode_sensor_test"
    root = Path(__file__).resolve().parent.parent / "custom_components" / "radiacode"
    module(package, __path__=[str(root)])
    module(f"{package}.radiacode_ble", __path__=[])
    monkeypatch.setitem(sys.modules, f"{package}.radiacode_ble.protocol", protocol)
    module(f"{package}.coordinator", RadiaCodeCoordinator=object)

    # Load constants through the isolated package too, avoiding the real
    # integration __init__ (which would import HA, bleak and config flows).
    for name in ("const", "sensor"):
        spec = importlib.util.spec_from_file_location(f"{package}.{name}", root / f"{name}.py")
        loaded = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, loaded)
        spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def coordinator():
    """A coordinator that retains each spectrum snapshot between other polls."""
    listeners = []

    def add_listener(listener):
        listeners.append(listener)
        return lambda: listeners.remove(listener)

    return SimpleNamespace(
        data=SimpleNamespace(spectrum=None),
        last_update_success=True,
        async_add_listener=add_listener,
        notify=lambda: [listener() for listener in listeners],
    )


def make_sensor(sensor_module, coordinator):
    entry = SimpleNamespace(data={"address": "AA:BB:CC:DD:EE:FF", "name": "RC-103"})
    return sensor_module.RadiaCodeSpectrumSensor(coordinator, entry)


def make_spectrum(protocol, **changes):
    fields = dict(duration_s=60, a0=-2.0, a1=3.0, a2=0.001, counts=[1] * 1024)
    fields.update(changes)
    return protocol.Spectrum(**fields)


def test_initial_unknown_spectrum(sensor_module, coordinator):
    sensor = make_sensor(sensor_module, coordinator)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}
    assert sensor.available


def test_full_spectrum_attributes_are_cached(sensor_module, coordinator, protocol):
    spectrum = make_spectrum(protocol)
    coordinator.data.spectrum = spectrum
    sensor = make_sensor(sensor_module, coordinator)
    attributes = sensor.extra_state_attributes

    assert sensor.native_value == 1024
    assert attributes == {
        "duration_s": 60, "channel_count": 1024,
        "calibration_a0": -2.0, "calibration_a1": 3.0, "calibration_a2": 0.001,
        "truncated": False, "channels": [1] * 1024,
    }
    assert sensor.extra_state_attributes is attributes
    assert attributes["channels"] is not spectrum.counts
    assert sensor._unrecorded_attributes == frozenset({"channels"})

    # A source mutation must not silently alter a state already published.
    spectrum.counts[0] = 100
    assert sensor.native_value == 1024
    assert attributes["channels"][0] == 1


def test_regular_polls_do_not_republish_or_traverse_cached_spectrum(
    sensor_module, coordinator, protocol,
):
    class CountedList(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    counts = CountedList([1] * 1024)
    coordinator.data.spectrum = make_spectrum(protocol, counts=counts)
    sensor = make_sensor(sensor_module, coordinator)
    asyncio.run(sensor.async_added_to_hass())
    initial_iterations = counts.iterations

    for _ in range(12):
        # New coordinator containers for faster-changing sensor data still
        # contain the same spectrum object until the next spectrum read.
        coordinator.data = SimpleNamespace(spectrum=coordinator.data.spectrum)
        coordinator.notify()
        assert sensor.native_value == 1024
        assert sensor.extra_state_attributes["channel_count"] == 1024

    assert sensor.writes == []
    assert counts.iterations == initial_iterations


def test_new_snapshot_updates_even_with_unchanged_total(sensor_module, coordinator, protocol):
    coordinator.data.spectrum = make_spectrum(protocol)
    sensor = make_sensor(sensor_module, coordinator)
    asyncio.run(sensor.async_added_to_hass())
    coordinator.data.spectrum = make_spectrum(protocol, duration_s=120, counts=[2, 0] * 512)
    coordinator.notify()

    assert len(sensor.writes) == 1
    assert sensor.native_value == 1024
    assert sensor.extra_state_attributes["duration_s"] == 120
    assert sensor.extra_state_attributes["channels"] == [2, 0] * 512


def test_availability_changes_publish_with_same_snapshot(sensor_module, coordinator, protocol):
    coordinator.data.spectrum = make_spectrum(protocol)
    sensor = make_sensor(sensor_module, coordinator)
    asyncio.run(sensor.async_added_to_hass())

    coordinator.last_update_success = False
    coordinator.notify()
    coordinator.notify()
    coordinator.last_update_success = True
    coordinator.notify()
    coordinator.notify()

    assert [(available, value) for available, value, _ in sensor.writes] == [
        (False, 1024), (True, 1024),
    ]


@pytest.mark.parametrize("empty_data", [None, SimpleNamespace(spectrum=None)])
def test_removed_snapshot_clears_value_and_attributes(
    sensor_module, coordinator, protocol, empty_data,
):
    coordinator.data.spectrum = make_spectrum(protocol)
    sensor = make_sensor(sensor_module, coordinator)
    asyncio.run(sensor.async_added_to_hass())
    coordinator.data = empty_data
    coordinator.notify()

    assert sensor.writes == [(True, None, {})]
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_latest_data_prepared_before_initial_ha_write(sensor_module, coordinator, protocol):
    sensor = make_sensor(sensor_module, coordinator)
    coordinator.data.spectrum = make_spectrum(protocol, duration_s=120)
    coordinator.last_update_success = False
    asyncio.run(sensor.async_added_to_hass())

    assert sensor.native_value == 1024
    assert sensor.extra_state_attributes["duration_s"] == 120
    coordinator.notify()
    assert sensor.writes == []
    coordinator.last_update_success = True
    coordinator.notify()
    assert sensor.writes[0][0] is True
