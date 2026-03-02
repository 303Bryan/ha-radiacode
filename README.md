# RadiaCode for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/303Bryan/ha-radiacode)](https://github.com/303Bryan/ha-radiacode/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Validate](https://github.com/303Bryan/ha-radiacode/actions/workflows/validate.yml/badge.svg)](https://github.com/303Bryan/ha-radiacode/actions/workflows/validate.yml)

A custom Home Assistant integration for **RadiaCode** radiation detectors (RC-102, RC-103, RC-110) via Bluetooth Low Energy.

Connects wirelessly using HA's built-in Bluetooth stack — works with local Bluetooth adapters and [ESPHome Bluetooth proxies](https://esphome.io/components/bluetooth_proxy.html) for whole-home coverage.

---

## Features

- **Real-time radiation monitoring** — dose rate (µSv/h) and count rate (CPS) updated every 15 seconds
- **Accumulated dose tracking** — total dose since the device was last reset
- **Device diagnostics** — battery level and internal temperature
- **Auto-discovery** — HA automatically detects RadiaCode devices over Bluetooth
- **BT proxy support** — works through ESPHome Bluetooth proxies; no direct Bluetooth adapter required on the HA host
- **Persistent connection** — keeps the BLE link open between polls to minimise reconnect overhead

---

## Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| Dose Rate | µSv/h | Real-time ambient radiation dose rate |
| Count Rate | cps | Raw detector counts per second |
| Accumulated Dose | µSv | Total dose accumulated since last device reset |
| Battery | % | Device battery level *(diagnostic)* |
| Temperature | °C | Device internal temperature *(diagnostic)* |

> **Note:** Dose Rate and Count Rate update every ~15 s. Battery, Temperature, and Accumulated Dose are reported by the device approximately once per minute and are cached between updates.

---

## Requirements

- **Home Assistant** 2024.1.0 or newer
- **RadiaCode** RC-102, RC-103, or RC-110 with firmware ≥ 4.8
- **Bluetooth** — one of:
  - A Bluetooth adapter on your HA host (USB dongle or built-in), **or**
  - One or more [ESPHome Bluetooth proxies](https://esphome.io/components/bluetooth_proxy.html) within range of the device

The RadiaCode does **not** need to be paired with the RadiaCode phone app to work with this integration.

---

## Installation

### Via HACS (Recommended)

1. Open HACS in Home Assistant → **Integrations**
2. Click the three-dot menu (⋮) → **Custom repositories**
3. Add `https://github.com/303Bryan/ha-radiacode` with category **Integration**
4. Click **RadiaCode** in the integration list → **Download**
5. Restart Home Assistant

### Manual

1. Download the [latest release](https://github.com/303Bryan/ha-radiacode/releases/latest)
2. Copy the `custom_components/radiacode/` folder into your HA `config/custom_components/` directory
3. Restart Home Assistant

---

## Configuration

### Automatic Discovery

If HA detects your RadiaCode over Bluetooth, a notification will appear on the **Integrations** page:

1. Go to **Settings → Devices & Services**
2. Click **Configure** on the discovered RadiaCode device
3. Confirm to add it

### Manual Setup

If auto-discovery doesn't trigger (common with ESPHome BT proxies where the device may not be actively advertising):

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **RadiaCode**
3. Enter the Bluetooth MAC address of your device

**Finding the MAC address:**
- In the RadiaCode mobile app: **Settings → Device Info**
- In your phone's Bluetooth settings when the device is paired
- In the HA Bluetooth integration's device list under **Settings → System → Bluetooth**

---

## ESPHome Bluetooth Proxy Setup

For the best results with BT proxies:

1. Flash an ESP32 board with the [ESPHome Bluetooth proxy firmware](https://esphome.github.io/bluetooth-proxies/)
2. Place the proxy within ~5 m of your RadiaCode device
3. Ensure the proxy is added to HA (it will appear as an ESPHome device)

**Tips for reliable operation:**
- Keep the proxy within strong signal range of the RadiaCode (RSSI better than −80 dBm)
- Each ESP32 proxy supports up to 3 simultaneous BLE connections — don't overload it with other BLE devices
- If the sensor shows unavailable periodically, the BLE link is dropping; move the proxy closer

---

## Known Limitations

- **Dose rate display** — Dose Rate may read `0.0000 µSv/h` on the first few polls after connection. This is because the device buffers data between polls; on the first read, the buffer contains only historical records that haven't yet built up a meaningful dose rate. It corrects itself within a minute of normal operation.
- **BT proxy notification buffer** — ESPHome proxies can forward approximately 28 BLE notification packets per transfer. For large data buffers (accumulated while the device was disconnected), the integration automatically uses whatever data arrived before the buffer filled. No data is lost; the next poll will catch up.
- **RareData update rate** — Battery, Temperature, and Accumulated Dose are updated by the device approximately once per minute, regardless of the poll interval.
- **Single connection** — The RadiaCode can only maintain one BLE connection at a time. While this integration is connected, the RadiaCode mobile app will not be able to connect to the device (and vice versa).

---

## Troubleshooting

### Sensor goes unavailable periodically

This usually means the BLE link is dropping. Check:
- **RSSI** — look in HA logs for `RSSI=` values on the proxy. Below −85 dBm is marginal; below −95 dBm is unreliable. Move the proxy closer.
- **Proxy slot usage** — the log will show `slots=X/3 free`. If you see `0/3 free` consistently, other BLE devices are competing for the proxy's connection slots.
- **Device battery** — a low battery can cause the RadiaCode to disconnect unexpectedly.

### Integration fails to set up / "Cannot connect"

- Confirm the RadiaCode is powered on and not connected to another device (phone app, etc.)
- Verify the MAC address is correct
- Check HA logs (`Settings → System → Logs`) for detailed error messages
- Try moving a Bluetooth proxy closer to the device

### Enabling debug logging

Add to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.radiacode: debug
```

Then restart HA and reproduce the issue. Logs appear under **Settings → System → Logs**.

---

## Contributing

Bug reports and pull requests are welcome! Please open an [issue](https://github.com/303Bryan/ha-radiacode/issues) before starting large changes.

The BLE protocol implementation is based on reverse-engineering work from:
- [cdump/radiacode](https://github.com/cdump/radiacode) — Python library (MIT)
- [mkgeiger/RadiaCode](https://github.com/mkgeiger/RadiaCode) — Arduino/ESP32 library (MIT)

---

## License

[MIT](LICENSE) © 2025 Bryan Fleming
