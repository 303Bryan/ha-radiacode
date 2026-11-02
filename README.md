# Radiacode for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/303Bryan/ha-radiacode)](https://github.com/303Bryan/ha-radiacode/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)
[![Validate](https://github.com/303Bryan/ha-radiacode/actions/workflows/validate.yml/badge.svg)](https://github.com/303Bryan/ha-radiacode/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Monitor a Radiacode radiation detector in Home Assistant over Bluetooth Low Energy. Use a local Bluetooth adapter or an [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy/).

- Dose rate, count rate, accumulated dose, and measurement-quality indicators.
- Gamma spectrum with dashboard examples and an on-demand action.
- Battery, temperature, connection, and device-health diagnostics.
- Display, sound, vibration, alarm-threshold, and reset controls.
- Bluetooth discovery and a connection switch to release the detector for its mobile app.

## Requirements and compatibility

- **Home Assistant 2026.9.4 or newer.**
- A compatible Radiacode 1XX detector and a Bluetooth adapter or an ESPHome proxy with **active connections enabled**.
- The detector must be free to connect: close other Bluetooth clients first. Pairing through the Radiacode phone app is not required.

| Device / firmware | Evidence and scope |
| --- | --- |
| RC-103 / 4.15 | Physical overnight testing through an ESPHome proxy; see the [stable field assessment](docs/stable-field-review-2026-11-02.md). |
| RC-103 / 4.14 | Earlier physical testing; see the [October field assessment](docs/stable-field-review-2026-10-11.md). |
| RC-101, RC-102, RC-103G, RC-110 / exact known 4.15 builds | Bundled layouts and automated fixtures. This does not establish physical acceptance for each model/proxy combination. |
| Other 1XX firmware builds | Configuration discovery and legacy decoding paths remain available; compatibility depends on the record layout. Report unrecognized builds with diagnostics. |
| Radiacode ZERO | Not supported by this release. |

See [firmware evidence and build matching](docs/firmware-415-beta-2026-11-01.md) for the supported 4.15 declarations. A matching version number alone does not establish a matching firmware build.

## Install

### HACS

This is currently a **custom repository** in HACS.

1. Open HACS, then its three-dot menu → **Custom repositories**.
2. Add `https://github.com/303Bryan/ha-radiacode` with category **Integration**.
3. Find **Radiacode** and download the latest stable release.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services** and configure the discovered detector. If it is not discovered, select **Add Integration → Radiacode** and enter its Bluetooth address.

### Manual

1. Download the installation ZIP or source archive from the [latest release](https://github.com/303Bryan/ha-radiacode/releases/latest).
2. Extract it and copy its `custom_components/radiacode/` directory to `config/custom_components/radiacode/` in Home Assistant.
3. Restart Home Assistant and add the integration as described above.

## Use

Dose and count readings poll every five seconds by default. Spectrum acquisition defaults to once per minute. Change these intervals through the integration's **Configure** options; a spectrum interval of `0` disables automatic spectrum reads.

The integration keeps its Bluetooth connection open. Turn off **BLE Connection** to use the mobile app, then turn it on again to resume Home Assistant polling.

- [Entities, controls, options, and spectrum action](docs/usage.md)
- [Spectrum dashboard examples](examples/spectrum-panel.yaml)
- [Troubleshooting, Bluetooth proxies, and diagnostics](docs/troubleshooting.md)
- [All documentation and field reports](docs/README.md)

## Measurement behavior

- Radiation readings expire after 60 seconds without a new accepted sample, or three poll intervals if longer. Brief reconnects may retain a recent sample; Bluetooth connectivity alone does not mean the measurement is fresh.
- Calculated alarm levels use the detector's configured thresholds. They do not reproduce the detector's latched, silenced, or acknowledged alarm state.
- A reading more than 50× above its baseline is held for confirmation by a subsequent measurement. This can delay publication of a sudden increase.
- Incomplete spectrum transfers keep the previous complete histogram. Check its acquisition time and retry/pause status.
- Interrupted reads may lose device records; reconnecting cannot guarantee recovery of every missed measurement.

Details and recovery steps are in [troubleshooting](docs/troubleshooting.md).

## Updates and removal

Update through HACS, then restart Home Assistant. Stable releases do not require enabling beta versions. Release notes identify the minimum Home Assistant version and any migration steps.

To remove a detector, delete its entry under **Settings → Devices & Services → Radiacode**. This also removes its private configuration cache. To uninstall the integration, remove all its entries, remove its files through HACS (or delete the manually installed `custom_components/radiacode/` directory), and restart Home Assistant.

## Contributing and support

[Report a problem](https://github.com/303Bryan/ha-radiacode/issues/new/choose) with your device, firmware, integration, Home Assistant, and proxy versions. Review logs for identifying information before uploading them.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for development and testing, [the architecture guide](docs/architecture.md) for implementation details, and [release versioning](docs/release-versioning.md) for publishing procedures.

## License and acknowledgments

[MIT](LICENSE) © Bryan Fleming.

The BLE protocol implementation builds on reverse-engineering work from [cdump/radiacode](https://github.com/cdump/radiacode) and [mkgeiger/RadiaCode](https://github.com/mkgeiger/RadiaCode).
