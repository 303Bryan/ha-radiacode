# Using Radiacode

[Documentation index](README.md) · [Installation](../README.md)

## Setup notes

Use Home Assistant 2026.9.4 or newer with a Radiacode 1XX detector and a local Bluetooth adapter or ESPHome proxy. Active Bluetooth connections must be enabled on the proxy. Close other Bluetooth clients before connecting; pairing through the Radiacode phone app is not required. Radiacode ZERO is not supported by this release.

## Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| Dose Rate | µSv/h | Real-time ambient radiation dose rate |
| Count Rate | cps | Raw detector counts per second |
| Accumulated Dose | µSv | Total dose accumulated since last device reset |
| Dose Counter | µSv | Independent non-resettable counter on supported firmware; unaffected by Reset Accumulated Dose |
| Hardness | — | Dose-rate/count-rate ratio using the device convention (µR/h ÷ cps) |
| Battery | % | Device battery level *(diagnostic)* |
| Temperature | °C | Device internal temperature *(diagnostic)* |
| Signal Strength | dBm | BLE RSSI from advertisements *(diagnostic)* |
| SiPM Bias Voltage | mV | Detector bias voltage *(diagnostic)* |
| MCU Temperature | °C | Processor temperature *(diagnostic, disabled by default)* |
| MCU Vref | mV | Processor reference voltage *(diagnostic, disabled by default)* |

> Device-health sensors are read once per minute and are hardware-verified on RC-103 firmware 4.14. Enable the disabled-by-default entities under the entity settings if you want them.

### Alarms

The three calculated alarm sensors use consistent names across Sensors and Configuration:

| Alarm sensor | Threshold controls | Unit |
|---|---|---|
| Accumulated Dose Alarm | Accumulated Dose Alarm L1 / L2 | µSv |
| Count Rate Alarm | Count Rate Alarm L1 / L2 | cps |
| Dose Rate Alarm | Dose Rate Alarm L1 / L2 | µSv/h |

Each shows `No Alarm`, `L1 Alarm`, or `L2 Alarm`, comparing its measurement with the thresholds **read from the device**. All expose `source: calculated`; they do not reproduce the firmware's latched, silenced, acknowledged, or out-of-scale states. Sound/vibration settings do not suppress these calculated states. Missing measurements or thresholds do not imply `No Alarm`.

**Names and existing automations:** Dose Rate Alarm was previously named Radiation Alarm; the accumulated-dose threshold controls were Dose Alarm L1/L2. Unique IDs, existing entity IDs, state strings and threshold attribute keys are preserved. User-customized display names remain user-controlled. The reset buttons are now Reset Accumulated Dose and Reset Spectrum.

**Grouping:** Home Assistant fixes the device-page sections (Sensors, Configuration, Diagnostic); an integration cannot insert its own Alarms subsection beneath Sensors. Alarm states remain primary sensors, and writable thresholds remain configuration entities. For a dedicated **Alarms** section below **Sensors** on a dashboard, use [the built-in-card example](../examples/alarms-card.yaml). It groups each alarm with its two threshold controls and requires no custom frontend integration. Replace the example IDs with the existing IDs from your device.

**Freshness:** Accumulated Dose and Accumulated Dose Alarm show the last observed since-reset total. Sparse counter updates no longer cause a separate 30-minute timeout: both stay available while accepted live dose/count measurements continue. The total and its alarm are based on the last actual observation, not an integrated estimate or a continuously sampled counter. Source and actual observation age remain visible in attributes and diagnostics.

A live-stream outage at the normal measurement limit (at least 60 seconds) makes both dose entities unavailable. When live readings return, a new dose record, successful register read, or confirmed dose reset is required; old counter data cannot revive itself. Brief reconnects within the live lease preserve availability. Turning BLE Connection off also requires a new observation after reconnecting. Initial startup can take tens of minutes to receive a dose record on firmware that rejects register reads. Three failed fallback reads pause that operation until reload or a confirmed firmware change. The separate non-resettable Dose Counter is never substituted. Dose Rate Alarm and Count Rate Alarm retain the live paired-sample lease.

The RC-103/4.15 firmware defaults are **0.40 / 1.20 µSv/h**, **20 / 60 cps**, and **9,999,999.99 / 9,999,999.99 µSv** for dose-rate, count-rate and accumulated-dose thresholds respectively. These are device defaults, not recommended exposure limits. The integration reads the actual settings and does not reset them during installation. The accumulated-dose input range includes those defaults.

### Measurement quality and detector temperature (1XX)

Dose Rate and Count Rate include `uncertainty_percent`, `quality`, `quality_reasons`, and `quality_source: calculated`. The **Dose Rate Quality** and **Count Rate Quality** diagnostic sensors make the same status available to automations. A fresh low-confidence reading remains visible; this feature does not smooth or clamp measurements.

| Quality | Meaning |
|---------|---------|
| `normal` | Uncertainty is within its limit and a recent detector-temperature observation supports normal quality |
| `reduced` | Dose-rate uncertainty exceeds 30%, count-rate uncertainty exceeds 50%, or detector temperature exceeds 45 °C |
| `unknown` | Required information is missing, stale, or inconclusive; the quality entities show HA's unknown state |

The error percentage follows the exact accepted reading. Raw-data fallbacks lack an uncertainty field. Temperature evidence expires after 180 seconds; missing data is never treated as zero error. At exactly 45 °C, quality remains unknown because the manufacturer manual and release notes differ at that boundary. Values below −20 °C also cannot establish normal quality. MCU temperature is not used.

**Detector Temperature Status** shows `normal`, `low` (below −20 °C), or `high` (above 45 °C), with calculated source, limits, and observation age in attributes. It becomes unknown when temperature evidence expires. Its inclusive range boundaries describe the documented temperature alarm limits, while the quality indicator handles the separate 45 °C uncertainty described above.

Firmware is checked again after reconnect with bounded retries. A confirmed change refreshes dependent configuration and cached metadata. Recent raw device-event codes/flags are available in downloadable diagnostics; their acknowledgment semantics are not inferred. These changes target 1XX detectors; ZERO support remains a separate update. See the [stable field assessment](stable-field-review-2026-11-02.md).

### Gamma Spectrum

The **Spectrum** sensor exposes the device's gamma spectrum (default: refreshed every 60 s, configurable in the options). Its state is the total count; the full per-channel histogram and the channel→keV calibration live in attributes:

| Attribute | Description |
|-----------|-------------|
| `channels` | Per-channel counts (1024 bins; excluded from the recorder database) |
| `calibration_a0/a1/a2` | Energy calibration: E(ch) = a0 + a1·ch + a2·ch² keV |
| `duration_s` | Spectrum accumulation time in seconds |
| `truncated` | False for published snapshots; incomplete reads keep the previous complete snapshot |
| `acquired_at` | UTC time the last complete current-spectrum snapshot was received; empty before the first acquisition or after Reset Spectrum |
| `automatic_paused` | True while normal spectrum polling is paused after repeated failures |
| `automatic_recovery_pending` | True when timed recovery attempts are enabled during the pause |

A **Reset Spectrum** button clears the accumulation, and the **`radiacode.get_spectrum` action** returns the spectrum as response data (`accumulated: true` for the device's long-term accumulated spectrum) for use in scripts and automations.

Open the Spectrum entity to inspect `acquired_at` and `automatic_paused`. Acquisition time is when Home Assistant received the complete snapshot, not when accumulation started; `duration_s` describes the accumulation period. Failed reads retain the last complete histogram and its acquisition time. After three failures, a single automatic recovery attempt becomes eligible after at least 30 minutes and 60 seconds of healthy live readings on the current connection. Further failures delay the next attempt by up to one hour. A successful current-spectrum read advances the time and clears the pause; an accumulated-spectrum action does not replace it. These attributes also appear before a histogram is available. A false pause flag does not override a spectrum interval of 0, which disables automatic reads.

**Plotting the spectrum (recommended panel view)** — install [Plotly Graph Card](https://github.com/dbuezas/lovelace-plotly-graph-card) from HACS, then use a dedicated [Panel view](https://www.home-assistant.io/dashboards/panel/) for a full-width graph. The example sets the graph height to **800 pixels** and reduces the margins to give the spectrum more vertical space.

Replace `sensor.radiacode_spectrum` with your device's actual Spectrum entity ID (shown in Settings → Devices & Services → Entities). Add the example as a **dashboard view**; when editing the full dashboard configuration, add it as an item under `views:` with the appropriate list indentation. The copyable view example is also available in [examples/spectrum-panel.yaml](../examples/spectrum-panel.yaml).

Adjust `layout.height` to suit your screen, for example `600` or `1000`. The height belongs inside the Plotly card's `layout` section. Axis labels use `title.text` for compatibility with current Plotly versions.

For a graph within an existing dashboard view, paste [examples/spectrum-card.yaml](../examples/spectrum-card.yaml) into a **Manual card**. It uses the same height and margins without the outer panel-view settings.

> Keep the `"y"` key quoted when copying or editing the card. If a saved card contains a `true` key in its place, rename it to `"y"`; Plotly needs both `x` and `y` arrays when `raw_plotly_config` is enabled.

Large spectrum reads can fail on weak or busy Bluetooth links. The integration keeps the previous complete histogram and backs off retries, pausing after three consecutive failures. Check acquisition time, pause status, and diagnostics; see [spectrum recovery](troubleshooting.md#spectrum-stays-unchanged-through-a-bluetooth-proxy).

Firmware layout selection and private configuration caching are automatic. Exact known 4.15 builds can use bundled declarations; other builds use bounded configuration discovery. See [firmware declarations](firmware-415-beta-2026-11-01.md) and [firmware architecture](architecture.md#firmware-declarations-and-private-storage) for details.

### Binary Sensors

| Entity | Description |
|--------|-------------|
| BLE Connected | True while the BLE link is active; exposes `last_error`, `connection_count`, and `last_poll_duration` attributes *(diagnostic)* |

> **Note:** Dose Rate and Count Rate update on every poll (default every 5 s, configurable). Battery, Temperature, and Accumulated Dose arrive in periodic device status records and are cached between updates. Sparse accumulated-dose records use the bounded register fallback described above. The independent Dose Counter updates much less often at background radiation levels and is cached separately. It remains Unknown on firmware without a recognized counter schema.

---

## Device Controls

The integration exposes the following device settings and reset controls.

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
| Accumulated Dose Alarm L1 | µSv | 0–10,000,000 | Level 1 accumulated dose alarm threshold |
| Accumulated Dose Alarm L2 | µSv | 0–10,000,000 | Level 2 accumulated dose alarm threshold |

### Selects

| Entity | Options | Description |
|--------|---------|-------------|
| Display Auto-Off | 5 s / 10 s / 15 s / 30 s | Display timeout duration |
| Display Orientation | Auto / Right / Left | Screen rotation mode |

### Buttons

| Entity | Description |
|--------|-------------|
| Reset Accumulated Dose | Clears the resettable accumulated dose; does not reset the independent Dose Counter |
| Reset Spectrum | Clears the current spectrum accumulation |

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
| Confirm Bluetooth writes (experimental) | Off | On / Off | Requests an acknowledgment for each command chunk. Test this only when comparing connection behavior; some proxies stall with it enabled. Changing the option reloads the integration. |

---

## On-demand spectrum action

Use `radiacode.get_spectrum` in a script or automation with a response variable:

```yaml
- action: radiacode.get_spectrum
  data:
    accumulated: false
  response_variable: spectrum
```

If multiple detectors are configured, add `address: "AA:BB:CC:DD:EE:FF"` under `data:` to select one. This action requires a response variable and returns `duration_s`, `channel_count`, `total_counts`, `calibration` (`a0`, `a1`, `a2`), `truncated`, and `channels`.

A current-spectrum read (`accumulated: false`) updates the current snapshot and, when successful, clears an automatic-acquisition pause. An accumulated-spectrum read returns the long-term spectrum without replacing the current snapshot or resuming its automatic acquisition.

For connection failures, stale measurements, or paused spectrum acquisition, see [troubleshooting](troubleshooting.md).

## Updates and removal

Update through HACS, then restart Home Assistant. Stable releases do not require enabling beta versions. Release notes identify the minimum Home Assistant version and any migration steps.

To remove a detector, delete its entry under **Settings → Devices & Services → Radiacode**. This also removes its private configuration cache. To uninstall the integration, remove all its entries, remove its files through HACS (or delete the manually installed `custom_components/radiacode/` directory), and restart Home Assistant.
