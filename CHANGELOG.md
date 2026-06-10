# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [1.0.0] — 2026-06-10

First stable 1.0 release — identical integration code to [1.0.0b1], promoted after validation on RC-103 hardware (FW 4.8) through an ESPHome BT proxy: options flow reload, dose reset, radiation alarm sensor, and diagnostics download all confirmed working.

Highlights since 0.6.4 (full details under [1.0.0b1]):

### Added
- **Configurable poll interval** (5–300 s) via a new options flow.
- **Radiation Alarm binary sensor** driven by the device's L1/L2 dose rate thresholds, with `alarm_level` attribute for automations.
- **Diagnostics support** — downloadable dump from the device page (address/name redacted).
- **Protocol test suite** — 33 pytest unit tests, run in CI alongside hassfest and HACS validation.
- **Release workflow** — manually-dispatched GitHub Action that tags `v<version>` from `manifest.json` and publishes a release with the matching CHANGELOG section as notes; PEP 440 pre-release versions are automatically marked as GitHub pre-releases for HACS beta users.

### Fixed
- Dose Reset now zeroes the Accumulated Dose sensor immediately.
- Clean `ConnectionError` (instead of `AttributeError` crash) when a BLE write races a disconnect.
- BLE link released on HA shutdown and entry unload, freeing the device for the mobile app.
- Manual config flow validates and normalises Bluetooth addresses.
- Deprecated `asyncio.get_event_loop()` replaced; corrupt notification packets guarded; manifest now depends on `bluetooth_adapters`.

---

## [1.0.0b1] — 2026-06-10

First 1.0 beta. Focus: configurability, observability, and robustness.

### Added
- **Configurable poll interval** — New options flow (Settings → Devices & Services → Radiacode → Configure) lets you set the BLE poll interval from 5 to 300 seconds. Longer intervals reduce BT proxy load and device battery drain.
- **Radiation Alarm binary sensor** — Turns on when the dose rate reaches the device's L1 alarm threshold; the active alarm level (0/1/2) and both thresholds (µSv/h) are exposed as attributes for automations. Computed in HA from device thresholds, so it works even with device sound/vibration off.
- **Diagnostics support** — Download a diagnostics dump (connection stats, last sensor/settings snapshot, options) from the device page. Bluetooth address and device name are redacted.
- **Protocol test suite** — 33 pytest unit tests covering command framing, response parsing, VSFR batch decoding, data_buf decoding, unit conversion, and settings/identity decoders. Runs in CI alongside hassfest and HACS validation.
- **Manual entry address normalisation** — Bluetooth addresses entered with dashes, dots, or no separators are normalised to colon format; invalid addresses are rejected with a clear error instead of creating a broken entry.

### Fixed
- **Dose Reset latency** — Pressing Dose Reset now zeroes the Accumulated Dose sensor immediately. Previously the cached pre-reset value kept showing for up to a minute (until the next RareData record).
- **Crash race on disconnect during command** — A BLE write racing with a disconnect could raise `AttributeError` (`NoneType.write_gatt_char`); it now raises a clean `ConnectionError` that the coordinator's retry logic handles.
- **Deprecated event-loop API** — `asyncio.get_event_loop()` inside the command loop replaced with `get_running_loop()` (the former is deprecated in coroutines and slated for removal).
- **Corrupt notification guard** — A malformed first notification packet declaring a negative body length is now ignored instead of corrupting reassembly state.
- **BLE teardown on shutdown/unload** — The BLE connection is now released on Home Assistant shutdown and config entry unload, freeing the device for the mobile app while HA is down. Entry unload no longer reaches into the client through a private attribute.
- **Bluetooth dependency** — Manifest now depends on `bluetooth_adapters` (the HA-recommended dependency for BLE integrations) instead of `bluetooth`.
- **Discovery UX** — The discovered-device card now shows the device name, and the confirm dialog is a proper single-button confirmation.

### Changed
- **README** — Corrected alarm threshold ranges, documented all diagnostic/connection entities, the new options flow, and filled in the previously empty debug-logging section.

---

## [0.6.4] — 2026-04-27

### Fixed
- **Bleak compatibility (#9)** — Replace deprecated `BleakClient.set_disconnected_callback()` (removed in bleak 1.0) with the `disconnected_callback=` argument to `establish_connection()`. The deprecated call raised `AttributeError` on recent installs before the init handshake ever ran, leaving every entity except RSSI permanently unavailable.
- **Init-failure diagnostics (#9)** — Each post-connect step (`service_discovery`, `start_notify`, `set_exchange`, `set_time`, `device_time`) is now wrapped in a `RadiaCodeInitError` carrying the failing step name. The coordinator surfaces it on the BLE Connected sensor's `last_error` attribute, so users (especially early RC-101 owners on FW 4.14) can see exactly where init fails without enabling debug logging.
- **GATT service verification (#9)** — After the BLE connection is established the client checks that the expected RadiaCode service UUID is present and logs all discovered services if not, instead of timing out 10 s later on the first `SET_EXCHANGE` write.

---

## [0.4.0] — 2026-03-06

### Added
- **Device controls** — Switches, numbers, selects, and buttons for full Radiacode configuration from HA (sound, vibration, display, brightness, alarm thresholds, orientation, dose reset).
- **Integration icon** — Radiation trefoil icon, light + dark theme (1× and 2×).
- **Temperature sensor** — Internal device temperature via VSFR `TEMP_degC`.

### Fixed
- **Dose rate unit conversion** — Raw `data_buf` dose_rate is in R/h; multiplied by 10,000 to produce correct µSv/h values (~0.10–0.30 µSv/h at background).
- **Accumulated dose unit** — Same ×10,000 conversion applied to `RareData.dose` (R → µSv).
- **DoseRateDB and RawData decoding** — Previously skipped record types now decoded and used as dose rate sources.
- **Write Without Response** — BLE writes use `response=False`; ATT Write Requests stalled 10+ s through ESPHome BT proxies.
- **BLE device lookup** — `async_ble_device_from_address()` called only on new connections, not every poll.
- **Partial VSFR batch responses** — Sensor registers marked invalid by firmware are gracefully skipped.
- **BLE command serialisation** — Commands queued to prevent framing corruption through BT proxies.

### Changed
- **Polling** — `data_buf` is now the primary source for dose rate, count rate, accumulated dose, and battery; only `TEMP_degC` still uses a VSFR batch read.
- **Branding** — Renamed "RadiaCode" → "Radiacode" throughout.

---

## [0.4.0b6] — 2026-03-06

### Changed
- **Branding** — Rename "RadiaCode" → "Radiacode" throughout (manifest, hacs.json, strings, translations, README).
- **Documentation** — Add Device Controls section to README covering all switch/number/select/button entities; remove now-resolved dose rate known limitation; full CHANGELOG history for all versions.
- **Icon** — README header now displays the integration icon so it renders correctly in HACS and GitHub.

---

## [0.4.0b5] — 2026-03-06

### Fixed
- **Dose rate unit conversion** — Dose rate was displaying `0.0000 µSv/h` because the raw `data_buf` float is in **R/h (Roentgen per hour)**, not µSv/h. Multiplying by 10,000 (= ×1,000,000 for µR/h, ÷100 for µSv/h) gives the correct value (e.g. ~0.10–0.30 µSv/h at background). Confirmed via cdump reference examples (`narodmon.py`: `1e6 * dose_rate` → µR/h; `webserver.py`: `1e4 * dose_rate` → µSv/h).
- **Accumulated dose unit** — Same ×10,000 conversion applied to `RareData.dose` (R → µSv).

---

## [0.4.0b4] — 2026-03-06

### Added
- **Diagnostic logging** — `decode_data_buf` now logs raw hex prefix, gid distribution, and per-record dose rate in scientific notation to aid unit investigation.

### Fixed
- **DoseRateDB and RawData decoding** — `data_buf` records of type DoseRateDB (gid=2) and RawData (gid=1) were previously skipped; they are now decoded and contribute to the dose rate reading.

### Changed
- **Simplified polling** — Removed broken individual VSFR reads (`RD_VIRT_SFR`, CMD 0x0824) for `DR_uR_h` and `DS_uR`; device firmware rejects these over BLE. Only `TEMP_degC` is still read via VSFR batch; all other values come from `data_buf`.

---

## [0.4.0b3] — 2026-03-06

### Fixed
- **Individual VSFR reads** — Added `CMD.RD_VIRT_SFR` (0x0824) as fallback for dose rate and accumulated dose when batch reads mark those registers invalid. (Superseded by b4 — device also rejects individual reads over BLE.)

---

## [0.4.0b2] — 2026-03-05

### Added
- **Device controls** — Exposes Radiacode configuration as writable HA entities:
  - **Switches**: Sound on/off, Vibration on/off, Display on/off, Display Backlight on/off
  - **Numbers**: Display Brightness (0–9), Dose Rate alarm thresholds L1/L2 (µSv/h), Count Rate alarm thresholds L1/L2 (cps), Accumulated Dose alarm thresholds L1/L2 (µSv)
  - **Selects**: Display Auto-Off time (5/10/15/30 s), Display Orientation (Auto/Right/Left)
  - **Button**: Reset Accumulated Dose

### Fixed
- **Partial VSFR batch responses** — The device marks sensor registers (DR_uR_h, DS_uR) as invalid in batch reads; these are now gracefully skipped rather than raising an error.
- **BLE command serialisation** — Concurrent BLE writes through ESPHome proxies could corrupt framing; commands are now queued and sent sequentially.

---

## [0.3.0] — 2026-03-05

### Added
- **Integration icon** — Radiation trefoil icon (light + dark theme, 1× and 2×) for HACS and the HA integrations page.
- **Temperature sensor** — Internal device temperature via VSFR `TEMP_degC` register.

### Fixed
- **Write Without Response** — BLE writes now use `response=False` (Write Without Response). ATT Write Requests (`response=True`) would stall 10+ seconds through ESPHome BT proxies.
- **BLE device lookup** — `async_ble_device_from_address()` is now called only when establishing a new connection, not on every poll. The previous behaviour caused false "not found" errors when the scanner was busy, killing healthy connections.

---

## [0.2.0] — 2026-03-02

### Fixed
- **Battery level** — was reporting 10,000% instead of 0–100%. The raw device value was being double-scaled.
- **Post-reconnect zero readings** — dose rate and count rate briefly showed 0.0 after reconnection. The coordinator now caches the last known good values and substitutes them until the device resumes streaming.
- **Disconnect timeout** — added 5-second timeout on `stop_notify()` and `disconnect()` to prevent hanging on dead BLE links.

### Changed
- **Poll interval** — reduced from 15 seconds to 5 seconds for faster updates.

---

## [0.1.0] — 2026-03-02

Initial public release.

### Added
- BLE integration for Radiacode RC-102, RC-103, and RC-110 devices
- **Dose Rate** sensor (µSv/h)
- **Count Rate** sensor (cps)
- **Accumulated Dose** sensor (µSv)
- **Battery** sensor (%)
- Auto-discovery via Home Assistant Bluetooth integration
- Manual MAC address entry for BT proxy environments
- Config flow with Bluetooth confirmation dialog
- Persistent BLE connection between polls
- Stall-based timeout detection for ESPHome BT proxy notification buffer limits
- Automatic retry on stale connection detection (same poll cycle recovery)
- GitHub Actions CI: hassfest + HACS validation

[Unreleased]: https://github.com/303Bryan/ha-radiacode/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v1.0.0
[1.0.0b1]: https://github.com/303Bryan/ha-radiacode/releases/tag/v1.0.0b1
[0.6.4]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.6.4
[0.4.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0
[0.4.0b6]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b6
[0.4.0b5]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b5
[0.4.0b4]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b4
[0.4.0b3]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b3
[0.4.0b2]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b2
[0.3.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.3.0
[0.2.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.2.0
[0.1.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.1.0
