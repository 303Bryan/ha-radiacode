# Radiacode for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/303Bryan/ha-radiacode)](https://github.com/303Bryan/ha-radiacode/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Monitor radiation, view gamma spectra, and control your Radiacode detector from Home Assistant. Connect through a local Bluetooth adapter or an [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy/).

- Dose rate, count rate, and accumulated dose.
- Gamma spectra with ready-to-use dashboard examples.
- Battery, temperature, and connection status.
- Display, sound, vibration, and alarm-threshold controls.

## Installation

Use Home Assistant **2026.9.4 or newer**.

### HACS

1. Open HACS → three-dot menu → **Custom repositories**.
2. Add `https://github.com/303Bryan/ha-radiacode` with category **Integration**.
3. Find **Radiacode** and download it.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services** and configure the discovered detector. If it does not appear, select **Add Integration → Radiacode** and enter its Bluetooth address.

### Manual

Copy `custom_components/radiacode/` from the [latest release](https://github.com/303Bryan/ha-radiacode/releases/latest) into Home Assistant's `config/custom_components/` directory. Restart Home Assistant, then add the integration under **Settings → Devices & Services**.

## Using Radiacode

Dose and count readings update every five seconds by default; spectra update once per minute. Adjust these intervals through the integration's **Configure** options. Set the spectrum interval to `0` to disable automatic spectrum reads.

To connect the Radiacode mobile app, turn off the **BLE Connection** switch in Home Assistant. Turn it back on when you want Home Assistant to reconnect.

## Alarm dashboard

Use [the alarm card example](examples/alarms-card.yaml) to place **Alarms** below **Sensors**, with Accumulated Dose Alarm, Count Rate Alarm, Dose Rate Alarm, and their threshold controls. It uses built-in cards; replace the example entity IDs with your detector's IDs. See [alarm behavior and device-page grouping](docs/usage.md#alarms).

## Spectrum dashboard

![Example gamma spectrum showing counts by energy in keV](docs/images/spectrum-dashboard.png)

Install [Plotly Graph Card](https://github.com/dbuezas/lovelace-plotly-graph-card) through HACS. Add the YAML below as a **dashboard view**; in the full dashboard configuration, add it as an item under `views:`. Replace `sensor.radiacode_spectrum` with your detector's Spectrum entity ID.

```yaml
type: panel
path: radiacode-spectrum
title: RadiaCode Spectrum
cards:
  - type: custom:plotly-graph
    title: Gamma Spectrum
    refresh_interval: 60
    raw_plotly_config: true
    entities:
      - entity: sensor.radiacode_spectrum
        name: Counts
        type: scatter
        mode: lines
        x: |
          $fn ({ hass, getFromConfig }) => {
            const sensor = hass.states[getFromConfig('.entity')];
            if (!sensor || ['unknown', 'unavailable'].includes(sensor.state)) return [];
            const a = sensor.attributes;
            return (a.channels || []).map((_, ch) =>
              a.calibration_a0 + a.calibration_a1 * ch + a.calibration_a2 * ch * ch);
          }
        "y": |
          $fn ({ hass, getFromConfig }) => {
            const sensor = hass.states[getFromConfig('.entity')];
            if (!sensor || ['unknown', 'unavailable'].includes(sensor.state)) return [];
            return sensor.attributes.channels || [];
          }
        hovertemplate: '%{x:.1f} keV<br>%{y} counts<extra></extra>'
    layout:
      height: 800
      margin:
        t: 30
        b: 60
        l: 70
        r: 30
      xaxis:
        type: linear
        title:
          text: Energy (keV)
        autorange: true
      yaxis:
        title:
          text: Counts
        rangemode: tozero
show_icon_and_title: false
```

[Download the view YAML](examples/spectrum-panel.yaml), or use the [card-only example](examples/spectrum-card.yaml) in an existing dashboard view.

## Documentation and support

- [Entities, controls, and configuration](docs/usage.md)
- [Troubleshooting and Bluetooth proxy setup](docs/troubleshooting.md)
- [Release notes](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Report a problem](https://github.com/303Bryan/ha-radiacode/issues/new/choose)

## License

[MIT](LICENSE) © Bryan Fleming. Protocol implementation builds on work from [cdump/radiacode](https://github.com/cdump/radiacode) and [mkgeiger/RadiaCode](https://github.com/mkgeiger/RadiaCode).

## External references

External sources supporting the current implementation, supported configurations, tests, dashboards, and maintenance workflow. Older commit-specific references remain where the current build still relies on that work.

### Radiacode documentation and applications

- **Manufacturer documentation:** [100-series overview](https://www.radiacode.com/100-series?lang=en), [device manual](https://downloads.radiacode.com/EN/RC-10x_Device_Manual.pdf), and [technical specifications](https://radiacode.com/docs/en/100-series/device/tech-specs) — device operation, measurement behavior, and specifications.
- **Measurement and display behavior:** [spectrum-channel documentation (PDF)](https://dl.radiacode.com/Radiacode_documentation_en.pdf), [Monitor mode](https://radiacode.com/docs/en/100-series/display-modes/monitor-mode), [Search mode](https://radiacode.com/docs/en/100-series/display-modes/search-mode), and [alarms](https://radiacode.com/docs/en/100-series/alarms/how-alarms-work/) — channel counts, averaging, display timing, and device alarm behavior.
- **Software and firmware evidence:** [official downloads](https://www.radiacode.com/downloads) and [manufacturer changelog](https://radiacode.com/changelog). Research examined Android 1.66.03, Windows 1.16.00/1.16.01, and the supplied macOS application 1.0.17, including bundled manuals and firmware declarations. Download pages may now serve different builds.

### Protocol libraries and independent research

- **[cdump/radiacode](https://github.com/cdump/radiacode)** — foundational Python protocol reference: [device commands](https://github.com/cdump/radiacode/blob/2b217916f49f5eedddf8f0d9116fcfd3c6b8832e/src/radiacode/radiacode.py), [Bluetooth framing](https://github.com/cdump/radiacode/blob/2b217916f49f5eedddf8f0d9116fcfd3c6b8832e/src/radiacode/transports/bluetooth.py), [spectrum decoder](https://github.com/cdump/radiacode/blob/2b217916f49f5eedddf8f0d9116fcfd3c6b8832e/src/radiacode/decoders/spectrum.py), [DATA_BUF decoder](https://github.com/cdump/radiacode/blob/2b217916f49f5eedddf8f0d9116fcfd3c6b8832e/src/radiacode/decoders/databuf.py), and [live-data example](https://github.com/cdump/radiacode/blob/2b217916f49f5eedddf8f0d9116fcfd3c6b8832e/src/radiacode/examples/webserver.py). [Issue #45](https://github.com/cdump/radiacode/issues/45) supplied additional alarm-event evidence.
- **[mkgeiger/RadiaCode](https://github.com/mkgeiger/RadiaCode)** — Arduino/ESP32 protocol and register reference, also used for independent transport comparison.
- **[petriska/qtradiacode](https://github.com/petriska/qtradiacode/tree/e71f13e23a01c2ba5ca6ab5167de06d88e2d741b)** and **[ckuethe/radiacode-tools](https://github.com/ckuethe/radiacode-tools)** — references for the retained serialized transport and separate acquisition tasks.
- **[Am6er/BecqMoni decoder](https://github.com/Am6er/BecqMoni/blob/11c4235f060873b910d7e5f36275e933f21939da/BecquerelMonitor/RadiaCodeIn.cs#L1511)** and [releases](https://github.com/Am6er/BecqMoni/releases) — compressed-spectrum encoding, signed deltas, and empty-group handling.
- **[darkmatter2222/Open-RadiaCode-Android decoder](https://github.com/darkmatter2222/Open-RadiaCode-Android/blob/2c8fd97d7f72c71dfab80e3b2b65367e1510e2c7/android_app/app/src/main/java/com/radiacode/ble/RadiacodeBleClient.kt#L433)** — independent spectrum-decoding comparison. This community project is separate from the manufacturer's Android application.
- **[darkmatter2222/radiacode-firmware](https://github.com/darkmatter2222/radiacode-firmware)** — firmware research, particularly the reviewed [container decoder](https://github.com/darkmatter2222/radiacode-firmware/blob/3a208390b146f3a0aa3caa80b6cdcd2840bce20c/pipeline/rcfmt.py) and [RC-102/4.14 capture](https://github.com/darkmatter2222/radiacode-firmware/blob/3a208390b146f3a0aa3caa80b6cdcd2840bce20c/firmware/rc-102/hw1-fw4.14/analysis/capture.json). [WeebDataHoarder/PhytonUtils](https://github.com/WeebDataHoarder/PhytonUtils) documents the container format credited by that decoder.

### Home Assistant and Bluetooth

- **Home Assistant integration guidance:** [Bluetooth](https://contributors.home-assistant.io/core/integrations/bluetooth/), [config flows](https://contributors.home-assistant.io/core/integrations/config-flow/), [manifests](https://contributors.home-assistant.io/core/integrations/manifest/), [fetching data](https://contributors.home-assistant.io/core/integrations/fetching-data/), [entity properties](https://contributors.home-assistant.io/core/entities/entity-class/), and [sensor entities](https://contributors.home-assistant.io/core/entities/sensor/) — discovery, setup, coordinated polling, availability, classification, and state semantics.
- **Home Assistant implementation references:** coordinator source for [2026.9.4](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/helpers/update_coordinator.py) and [2026.10.0](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/helpers/update_coordinator.py), [2026.10.4 storage](https://github.com/home-assistant/core/blob/2026.10.4/homeassistant/helpers/storage.py), and the [entity](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/helpers/entity_registry.py), [device](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/helpers/device_registry.py), and [base registry](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/helpers/registry.py) implementations — lifecycle, cache, and runtime-test investigations. The [Bluetooth](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/bluetooth/manifest.json) and [ESPHome](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/esphome/manifest.json) manifests record the dependency versions examined.
- **BLE transport:** [Bleak client API](https://bleak.readthedocs.io/en/latest/api/client.html#bleak.BleakClient.write_gatt_char), [bleak-retry-connector 4.7.1](https://github.com/Bluetooth-Devices/bleak-retry-connector/blob/v4.7.1/src/bleak_retry_connector/__init__.py), [habluetooth wrappers](https://github.com/Bluetooth-Devices/habluetooth/blob/c5b7eca3a35bfb4afcb55dccded730aa77d5e9d9/src/habluetooth/wrappers.py), and [bleak-esphome scanner](https://github.com/Bluetooth-Devices/bleak-esphome/blob/b2fb6bf4cb2ea4bf52284ce8664d11e3de46fdf4/src/bleak_esphome/backend/scanner.py) — write modes, retry behavior, backend selection, and proxy discovery. [Home Assistant's connection monitor](https://www.home-assistant.io/integrations/bluetooth/#connection-monitor) supports field diagnosis.

### ESPHome proxy investigations

- **Setup and configuration:** [Bluetooth proxy documentation](https://esphome.io/components/bluetooth_proxy/), [proxy installer](https://esphome.github.io/bluetooth-proxies/), [Native API](https://esphome.io/components/api/), [network configuration](https://esphome.io/components/network/#configuration-variables), and [2026.9.0 changelog](https://esphome.io/changelog/2026.9.0/) — proxy setup, connection limits, send queues, and the proposed TCP-buffer experiment.
- **Notification forwarding:** [2026.7.4 notification handler](https://github.com/esphome/esphome/blob/2026.7.4/esphome/components/bluetooth_proxy/bluetooth_connection.cpp#L477), [API send path](https://github.com/esphome/esphome/blob/2026.7.4/esphome/components/api/api_connection.cpp#L2149), [reviewed relay implementation](https://github.com/esphome/esphome/blob/2d56559f04d43095467c4a37c46ad5a97e471f18/esphome/components/bluetooth_connection/bluetooth_connection_hub.cpp#L360), [2026.9.0 warning implementation](https://github.com/esphome/esphome/blob/2026.9.0/esphome/components/bluetooth_connection/bluetooth_connection_hub.cpp#L360-L385), and [PR #18605](https://github.com/esphome/esphome/pull/18605) — source evidence used to investigate incomplete transfers and interpret forwarding warnings.

### Dashboards, packaging, and validation

- **Spectrum dashboard:** [dbuezas/Plotly Graph Card](https://github.com/dbuezas/lovelace-plotly-graph-card) and [Home Assistant Panel views](https://www.home-assistant.io/dashboards/panel/) — spectrum chart component and full-width dashboard layout.
- **HACS:** [custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/), [publishing and version requirements](https://www.hacs.xyz/docs/publish/start/#versions), [catalog inclusion requirements](https://github.com/hacs/documentation/blob/main/source/docs/publish/include.md), and [submission template](https://github.com/hacs/default/blob/master/.github/PULL_REQUEST_TEMPLATE.md) — installation and publication guidance.
- **Release conventions:** [Python version specification](https://packaging.python.org/en/latest/specifications/version-specifiers/) and [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) — version formatting and changelog conventions.
- **Validation tools:** [pytest](https://docs.pytest.org/en/stable/), [Home Assistant hassfest](https://github.com/home-assistant/actions/tree/master/hassfest), [HACS action](https://github.com/hacs/action), and GitHub Actions [checkout](https://github.com/actions/checkout) / [setup-python](https://github.com/actions/setup-python) — test execution, integration validation, and continuous-integration tooling.
- **Source attribution:** [GitHub permanent links](https://docs.github.com/en/repositories/working-with-files/using-files/getting-permanent-links-to-files), [CITATION files](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files), [REUSE guidance](https://reuse.software/faq/), and [MIT license terms](https://choosealicense.com/licenses/mit/) — citation, provenance, and notice-preservation guidance for contributors.
