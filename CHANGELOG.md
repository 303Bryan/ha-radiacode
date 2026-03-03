# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [0.2.0] — 2026-03-02

### Fixed
- **Battery level** — was reporting 10,000% instead of 0–100%. The raw device value was being double-scaled (divided by 100, then multiplied by 100 again).
- **Post-reconnect zero readings** — dose rate and count rate briefly showed 0.0 after a reconnection because the device buffer was empty. The coordinator now caches the last known good values and uses them until the device reports real data.
- **Disconnect timeout** — added 5-second timeout on `stop_notify()` and `disconnect()` calls to prevent hanging on dead BLE links.

### Changed
- **Poll interval** — reduced from 15 seconds to 5 seconds for faster count rate and dose rate updates.

---

## [0.1.0] — 2026-03-02

Initial public release.

### Added
- BLE integration for RadiaCode RC-102, RC-103, and RC-110 devices
- **Dose Rate** sensor (µSv/h) — real-time ambient radiation dose rate
- **Count Rate** sensor (cps) — raw detector counts per second
- **Accumulated Dose** sensor (µSv) — total dose since last device reset
- **Battery** sensor (%) — device battery level (diagnostic)
- **Temperature** sensor (°C) — device internal temperature (diagnostic)
- Auto-discovery via Home Assistant Bluetooth integration
- Manual MAC address entry for BT proxy environments
- Config flow with Bluetooth confirmation dialog
- Persistent BLE connection between polls (reduces reconnect overhead from ~15 s to ~3 s)
- Stall-based timeout detection for ESPHome BT proxy notification buffer limits
- Automatic retry on stale connection detection (same poll cycle recovery)
- Per-write timeout guard (prevents 30 s hangs on dead BLE links through proxies)
- 5-second delay before reconnect to allow BT proxy slot release
- GitHub Actions CI: hassfest + HACS validation on push and PR

### Technical notes
- BLE protocol reverse-engineered from [cdump/radiacode](https://github.com/cdump/radiacode) (MIT) and [mkgeiger/RadiaCode](https://github.com/mkgeiger/RadiaCode) (MIT)
- Uses `bleak` + `bleak-retry-connector` for HA Bluetooth proxy compatibility
- Requires firmware ≥ 4.8 on the RadiaCode device
- Minimum Home Assistant version: 2024.1.0

[0.2.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.2.0
[0.1.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v0.1.0
