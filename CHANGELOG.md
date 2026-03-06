# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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

[Unreleased]: https://github.com/303Bryan/ha-radiacode/compare/v0.4.0b6...HEAD
[0.4.0b6]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b6
[0.4.0b5]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b5
[0.4.0b4]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b4
[0.4.0b3]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b3
[0.4.0b2]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b2
[0.3.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.3.0
[0.2.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.2.0
[0.1.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.1.0
