# Radiacode for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/303Bryan/ha-radiacode)](https://github.com/303Bryan/ha-radiacode/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Validate](https://github.com/303Bryan/ha-radiacode/actions/workflows/validate.yml/badge.svg)](https://github.com/303Bryan/ha-radiacode/actions/workflows/validate.yml)

A custom Home Assistant integration for **Radiacode** radiation detectors via Bluetooth Low Energy.

Connects wirelessly using HA's built-in Bluetooth stack — works with local Bluetooth adapters and [ESPHome Bluetooth proxies](https://esphome.io/components/bluetooth_proxy.html) for whole-home coverage.

---

## Features

- **Real-time radiation monitoring** — dose rate (µSv/h) and count rate (CPS), polled every 5 seconds by default (configurable 5–300 s)
- **Radiation alarm** — binary sensor that trips when the dose rate crosses the device's alarm thresholds, for HA automations
- **Accumulated dose tracking** — total dose since the device was last reset
- **Device diagnostics** — battery level, internal temperature, BLE signal strength, and downloadable diagnostics
- **Device controls** — adjust display settings, alarm thresholds, sound/vibration, and more directly from HA
- **Auto-discovery** — HA automatically detects Radiacode devices over Bluetooth
- **BT proxy support** — works through ESPHome Bluetooth proxies; no direct Bluetooth adapter required on the HA host
- **Persistent connection** — keeps the BLE link open between polls to minimise reconnect overhead

---

## Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| Dose Rate | µSv/h | Real-time ambient radiation dose rate |
| Count Rate | cps | Raw detector counts per second |
| Accumulated Dose | µSv | Total dose accumulated since last device reset |
| Hardness | — | Spectral hardness coefficient (µR/h ÷ cps), as in the Radiacode app — characteristic per isotope, useful for pseudo-identification of sources |
| Battery | % | Device battery level *(diagnostic)* |
| Temperature | °C | Device internal temperature *(diagnostic)* |
| Signal Strength | dBm | BLE RSSI from advertisements *(diagnostic)* |
| SiPM Bias Voltage | mV | Detector bias voltage — drift indicates SiPM aging *(diagnostic)* |
| MCU Temperature | °C | Processor temperature *(diagnostic, disabled by default)* |
| MCU Vref | mV | Processor reference voltage *(diagnostic, disabled by default)* |

> Device-health sensors are read once per minute and are hardware-verified on RC-103 firmware 4.14. Enable the disabled-by-default entities under the entity settings if you want them.

### Radiation Alarm

The **Radiation Alarm** sensor shows `No Alarm`, `L1 Alarm`, or `L2 Alarm` based on the device's own L1/L2 dose rate thresholds (exposed as attributes in µSv/h). The comparison runs in HA, so automations trigger even when device sound/vibration are off.

### Gamma Spectrum

The **Spectrum** sensor exposes the device's gamma spectrum (default: refreshed every 60 s, configurable in the options). Its state is the total count; the full per-channel histogram and the channel→keV calibration live in attributes:

| Attribute | Description |
|-----------|-------------|
| `channels` | Per-channel counts (1024 bins; excluded from the recorder database) |
| `calibration_a0/a1/a2` | Energy calibration: E(ch) = a0 + a1·ch + a2·ch² keV |
| `duration_s` | Spectrum accumulation time in seconds |
| `truncated` | True when a BT-proxy transfer was cut short (leading channels still valid) |

A **Spectrum Reset** button clears the accumulation, and the **`radiacode.get_spectrum` action** returns the spectrum as response data (`accumulated: true` for the device's long-term accumulated spectrum) for use in scripts and automations.

**Plotting the spectrum** — with the community [ApexCharts card](https://github.com/RomRider/apexcharts-card):

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Gamma Spectrum
graph_span: 1h
update_interval: 60s
series:
  - entity: sensor.radiacode_spectrum
    name: Counts
    data_generator: |
      const a0 = entity.attributes.calibration_a0;
      const a1 = entity.attributes.calibration_a1;
      const a2 = entity.attributes.calibration_a2;
      return (entity.attributes.channels || []).map((count, ch) =>
        [a0 + a1 * ch + a2 * ch * ch, count]);
yaxis:
  - min: 0
apex_config:
  xaxis:
    type: numeric
    title:
      text: Energy (keV)
  chart:
    zoom:
      enabled: true
```

> **BT proxy note:** the spectrum is the largest BLE transfer this integration performs. Through an ESPHome proxy it may be truncated by the notification buffer — the decoded leading channels (where most background counts live) are kept and `truncated: true` is set. A direct Bluetooth adapter receives full spectra. Set the spectrum interval option to 0 to disable spectrum polling.

### Binary Sensors

| Entity | Description |
|--------|-------------|
| BLE Connected | True while the BLE link is active; exposes `last_error`, `connection_count`, and `last_poll_duration` attributes *(diagnostic)* |

> **Note:** Dose Rate and Count Rate update on every poll (default every 5 s, configurable). Battery, Temperature, and Accumulated Dose are reported by the device approximately once per minute and are cached between updates.

---

## Device Controls

The integration exposes the full Radiacode configuration as writable HA entities.

### Switches

| Entity | Description |
|--------|-------------|
| Sound | Enable/disable click sound on detection events |
| Vibration | Enable/disable vibration on detection events |
| Display | Turn the device display on or off |
| Display Backlight | Enable/disable display backlight |
| BLE Connection | Turn off to release the device so the Radiacode mobile app can connect; turn back on to resume polling |

### Numbers

| Entity | Unit | Range | Description |
|--------|------|-------|-------------|
| Display Brightness | — | 0–9 | Screen brightness level |
| Dose Rate Alarm L1 | µSv/h | 0–10,000 | Level 1 dose rate alarm threshold |
| Dose Rate Alarm L2 | µSv/h | 0–10,000 | Level 2 dose rate alarm threshold |
| Count Rate Alarm L1 | cps | 0–100,000 | Level 1 count rate alarm threshold |
| Count Rate Alarm L2 | cps | 0–100,000 | Level 2 count rate alarm threshold |
| Dose Alarm L1 | µSv | 0–1,000,000 | Level 1 accumulated dose alarm threshold |
| Dose Alarm L2 | µSv | 0–1,000,000 | Level 2 accumulated dose alarm threshold |

### Selects

| Entity | Options | Description |
|--------|---------|-------------|
| Display Auto-Off | 5 s / 10 s / 15 s / 30 s | Display timeout duration |
| Display Orientation | Auto / Right / Left | Screen rotation mode |

### Buttons

| Entity | Description |
|--------|-------------|
| Reset Accumulated Dose | Clears the accumulated dose counter on the device |

---

## Requirements

- **Home Assistant**
- **Radiacode** RC-102, RC-103, or RC-110 (tested with firmware 4.8 and 4.14)
- **Bluetooth** — one of:
  - A Bluetooth adapter on your HA host (USB dongle or built-in), **or**
  - One or more [ESPHome Bluetooth proxies](https://esphome.io/components/bluetooth_proxy.html) within range of the device

The Radiacode does **not** need to be paired with the Radiacode phone app to work with this integration.

---

## Installation

### Via HACS (Recommended)

1. Open HACS in Home Assistant → **Integrations**
2. Click the three-dot menu (⋮) → **Custom repositories**
3. Add `https://github.com/303Bryan/ha-radiacode` with category **Integration**
4. Click **Radiacode** in the integration list → **Download**
5. Restart Home Assistant

### Manual

1. Download the [latest release](https://github.com/303Bryan/ha-radiacode/releases/latest)
2. Copy the `custom_components/radiacode/` folder into your HA `config/custom_components/` directory
3. Restart Home Assistant

---

## Configuration

### Automatic Discovery

If HA detects your Radiacode over Bluetooth, a notification will appear on the **Integrations** page:

1. Go to **Settings → Devices & Services**
2. Click **Configure** on the discovered Radiacode device
3. Confirm to add it

### Manual Setup

If auto-discovery doesn't trigger:

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Radiacode**
3. Enter the Bluetooth MAC address of your device (e.g. `AA:BB:CC:DD:EE:FF` — dashes, dots, or no separators are also accepted)

### Options

After setup, click **Configure** on the integration to adjust:

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| Poll interval | 5 s | 5–300 s | How often sensor data is read over BLE. Longer intervals reduce BT proxy load and device battery drain. |
| Spectrum poll interval | 60 s | 0–3600 s | How often the gamma spectrum is read. 0 disables spectrum polling (the largest BLE transfer). |

---

## ESPHome Bluetooth Proxy Setup

For the best results with BT proxies:

1. Flash an ESP32 board with the [ESPHome Bluetooth proxy firmware](https://esphome.github.io/bluetooth-proxies/)
2. Place the proxy within ~5 m of your Radiacode device
3. Ensure the proxy is added to HA (it will appear as an ESPHome device)

**Tips for reliable operation:**
- Keep the proxy within strong signal range of the Radiacode (RSSI better than −80 dBm)
- Each ESP32 proxy supports up to 3 simultaneous BLE connections — don't overload it with other BLE devices
- If the sensor shows unavailable periodically, the BLE link is dropping; move the proxy closer

---

## Known Limitations

- **BT proxy notification buffer** — ESPHome proxies can forward approximately 28 BLE notification packets per transfer. For large data buffers (accumulated while the device was disconnected), the integration automatically uses whatever data arrived before the buffer filled. No data is lost; the next poll will catch up.
- **Outlier suppression delay** — a dose/count rate reading more than 50× above the current baseline is held back for one poll and shown only if the next poll confirms it. Genuine radiation events (which are sustained) appear at most one poll interval late; one-off corrupt values from truncated BLE transfers never reach the graph. Suppressed values are logged as warnings.
- **Signal Strength while connected** — a connected BLE peripheral stops advertising, so no fresh RSSI is available during an active connection; the sensor holds the last observed value until the next advertisement.
- **RareData update rate** — Battery, Temperature, and Accumulated Dose are updated by the device approximately once per minute, regardless of the poll interval.
- **Single connection** — The Radiacode can only maintain one BLE connection at a time. While this integration is connected, the Radiacode mobile app will not be able to connect to the device (and vice versa).

---

## Troubleshooting

### Sensor goes unavailable periodically

This usually means the BLE link is dropping. Check:
- **RSSI** — look in HA logs for `RSSI=` values on the proxy. Below −85 dBm is marginal; below −95 dBm is unreliable. Move the proxy closer.
- **Proxy slot usage** — the log will show `slots=X/3 free`. If you see `0/3 free` consistently, other BLE devices are competing for the proxy's connection slots.
- **Device battery** — a low battery can cause the Radiacode to disconnect unexpectedly.

### Integration fails to set up / "Cannot connect"

- Confirm the Radiacode is powered on and not connected to another device (phone app, etc.)
- Verify the MAC address is correct
- Check HA logs (`Settings → System → Logs`) for detailed error messages
- Try moving a Bluetooth proxy closer to the device

### Enabling debug logging

Add the following to your `configuration.yaml` and restart HA (or use **Settings → Devices & Services → Radiacode → Enable debug logging** for a temporary session):

```yaml
logger:
  logs:
    custom_components.radiacode: debug
```

Debug logs include per-poll timings, BLE notification reassembly details, and decoded record distributions — include them when filing an issue.

### Downloading diagnostics

From the device page, click the three-dot menu → **Download diagnostics** to get a JSON dump of connection statistics, the latest sensor/settings snapshot, device-health readings, and the device's self-describing **SFR register directory** — a listing of every register the firmware supports with its address, size, type, and signedness (Bluetooth address and device name are redacted).

---

## Contributing

Bug reports and pull requests are welcome! Please open an [issue](https://github.com/303Bryan/ha-radiacode/issues) before starting large changes.

The BLE protocol implementation is based on reverse-engineering work from:
- [cdump/radiacode](https://github.com/cdump/radiacode) — Python library (MIT)
- [mkgeiger/RadiaCode](https://github.com/mkgeiger/RadiaCode) — Arduino/ESP32 library (MIT)

---

## License

[MIT](LICENSE) © 2025 Bryan Fleming
