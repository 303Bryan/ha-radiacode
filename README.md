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

Use the [spectrum dashboard example](examples/spectrum-panel.yaml) to graph the detector's spectrum.

## Documentation and support

- [Entities, controls, and configuration](docs/usage.md)
- [Troubleshooting and Bluetooth proxy setup](docs/troubleshooting.md)
- [Release notes](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Report a problem](https://github.com/303Bryan/ha-radiacode/issues/new/choose)

## License

[MIT](LICENSE) © Bryan Fleming. Protocol implementation builds on work from [cdump/radiacode](https://github.com/cdump/radiacode) and [mkgeiger/RadiaCode](https://github.com/mkgeiger/RadiaCode).
