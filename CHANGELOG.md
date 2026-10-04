# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [2.0.2rc1] — 2026-10-03

### Fixed
- **Repeated spectrum reconnects** — pause automatic spectrum acquisition after three consecutive failures. Keep the last complete histogram and continue radiation polling. A successful manual current-spectrum read resumes acquisition; an explicit spectrum reset or integration reload permits a new bounded series of attempts. Manually disabled spectrum polling remains disabled.
- **Brief BLE outages** — retain the last accepted radiation sample for up to 60 seconds (or three configured poll intervals, if longer) during reconnects. An independent expiry timer makes stale measurements unavailable even while a reconnect is still running. Empty buffers and repeated samples never renew this window, and manually disabling BLE makes measurements unavailable immediately.
- **Slow bulk transfers** — allow up to 30 seconds for spectra, configuration and the register directory once response packets start arriving, while retaining the two-second packet-stall deadline and strict complete-frame validation. Ordinary commands, hung writes and reads without any reply retain their ten-second deadline.
- **Interrupted disconnect cleanup** — finish bounded BLE teardown before propagating repeated cancellation, helping release the proxy's connection slot during unload or user disconnect.

### Added
- Spectrum diagnostics report whether automatic acquisition is paused, consecutive failures and the retry limit. Transport diagnostics identify each command's deadline; radiation diagnostics distinguish retained readings from fresh measurements.
- Regression coverage for repeated spectrum failures, manual recovery, stalled versus progressing bulk transfers, and measurement expiry during connection recovery.

### Validation limits
- This is a release candidate for device testing. The reported disconnect intervals match the existing spectrum retry schedule (5, 10, 20, 40, then 60 minutes), which points to failed automatic spectrum transfers; device diagnostics are still needed to confirm that cause.
- Longer deadlines cannot recover notifications that a proxy drops. Pausing failed optional reads prevents an ongoing reconnect loop; it does not repair proxy forwarding loss.

---

## [2.0.1] — 2026-10-03

### Fixed
- **Spectrum acquisition and decoding** — request the histogram directly, accept compressed empty groups, and correctly decode absolute uint32 values and wrapping deltas. Publish only a complete, uniquely valid 1,024-channel spectrum; a successful manual current-spectrum action also updates the dashboard entity.
- **Spectrum chart example** — quote the Plotly `"y"` key so the count array remains assigned to the correct axis. Check the saved card configuration if its key was converted to `true`.
- **Delayed HA readings** — publish radiation measurements before optional identity, temperature, settings, health and spectrum requests. One managed maintenance task handles optional work and is cancelled on disconnect, unload and shutdown.
- **Measurement freshness** — keep dose and count paired, accept genuine zero readings, and expire cached radiation after three poll intervals (at least 15 seconds) without an advancing accepted measurement. Empty buffers, replays and suppressed outliers cannot renew freshness.
- **Alarm record alignment** — consume confirmed count-rate and dose-rate Event payloads so their extra bytes do not prevent decoding subsequent measurements. Unsupported masks stop conservatively.

### Added
- **Transfer diagnostics** — bounded command histories report targets, sequences, link generations, declared/received bytes, notification sizes, latency/gaps, errors and disconnect reasons. Diagnostics also expose measurement freshness, optional-operation status and spectrum attempt/success ages, with source identifiers redacted.
- **Protocol research and regression coverage** — captured-record and synthetic decoder cases, fake BLE transport tests, and coordinator/entity lifecycle tests using Home Assistant 2026.9.4 and Python 3.14.
- **Proxy troubleshooting** — guidance for correlating incomplete HA transfers with ESPHome notification-forwarding warnings and checking the age of the retained spectrum.

### Changed
- **HA requirement** — Home Assistant 2026.9.4 or newer, matching the validated background-task lifecycle.

### Hardware validation and remaining limitations
- RC-103G firmware 4.14 produced complete automatic and manual 1,024-channel spectra and a populated Home Assistant chart. Search mode at 0.5 seconds per bar scrolled steadily. Primary measurements updated at the configured five-second cadence, and one failed spectrum test recovered fresh readings within seven seconds.
- Some spectrum transfers still lose notifications through Bluetooth proxies. An ESPHome `Failed to send notify data response` warning was captured during an incomplete transfer. The integration rejects that transfer, retains the last complete spectrum, releases the affected connection and backs off automatic retries. This release improves decoding and recovery; proxy forwarding loss remains unresolved.
- The previously reported device error code was not captured, and prolonged connection stability has not been established. No proxy firmware or TCP-buffer change is part of this integration release.

---

## [2.0.1rc3] — 2026-10-03

### Fixed
- **Compressed spectra with empty groups** — accept zero-count groups as no-ops, matching the reference clients. A fully received firmware 4.14 spectrum began with two empty groups that RC1 and RC2 rejected. Each group still consumes its header; unsupported encodings, excess channels and incomplete histograms remain rejected.

### Validation limits
- Close-range tests reproduced missing notifications through two different proxies. One subsequent AIR-1 transfer arrived completely and exposed this decoder defect. RC3 still requires repeated complete spectra, a populated dashboard, device-display checks and prolonged connection testing before production promotion.

---

## [2.0.1rc2] — 2026-10-03

### Fixed
- **Measurement buffer alignment after alarms** — consume the confirmed count-rate and dose-rate payloads carried by Event records. RC1's four-byte Event assumption left six bytes unread on the captured count-alarm event and stopped decoding later measurements. Unverified channel masks stop conservatively.

### Added
- Regression coverage using the exact captured 17-byte count-alarm Event followed by a synthetic measurement record, plus legacy, dose-alarm, truncated and unsupported-mask cases.

### Validation limits
- RC1 hardware testing confirmed that spectrum transfers still lose notification packets through the installed proxy, despite bypassing configuration acquisition. RC2 corrects an additional decoder defect; proxy transport reliability and physical display behavior remain under investigation. Do not promote to production until hardware acceptance passes.

---

## [2.0.1rc1] — 2026-10-03

### Fixed
- **Spectrum acquisition** — request the histogram directly instead of requiring a large configuration download first. Validate both supported encodings and accept only a complete, uniquely valid 1,024-channel spectrum. Correct absolute uint32 and wrapping-delta decoding in compressed spectra.
- **Delayed HA readings** — publish DATA_BUF radiation readings before optional identity, temperature, settings, health and spectrum requests. One managed maintenance task handles those requests and is cancelled on disconnect, unload and shutdown.
- **Stale or mismatched measurements** — keep dose and count from the same latest valid record, accept genuine zero readings, and expire cached radiation after three poll intervals (at least 15 seconds) without an advancing measurement. Optional results cannot mark a stale or failed primary poll successful.
- **Manual spectrum action** — successful current-spectrum action results update the Spectrum entity used by dashboard cards.

### Added
- **Transfer diagnostics** — bounded command histories with targets, sequences, link generations, byte/fragment counts, latency/gaps, error categories and disconnect reasons; measurement freshness/provenance, optional-operation status and spectrum attempt/success ages. Source identifiers are redacted in downloadable diagnostics.
- **Research notes** — device/library/proxy evidence and unresolved protocol questions in `docs/protocol-and-stability-research.md`.
- **Real HA regression coverage** — test publication and cancellation using Home Assistant 2026.9.4 and Python 3.14, alongside decoder, fake-transport and captured-record regression tests.

### Changed
- **HA requirement** — Home Assistant 2026.9.4 or newer. The managed background task lifecycle is validated against that runtime; HACS no longer advertises incompatible HA 2024.1 support.

### Validation limits
- Release candidate for RC-103G firmware 4.14 and Bluetooth-proxy validation. Missing notification packets remain a transport failure; incomplete histograms are rejected. Repeated complete spectra, a populated physical dashboard, responsive device/LCD readings, and prolonged connection stability must be verified before promoting this candidate to production.

---

## [2.0.0] — 2026-10-03

### Fixed
- **Spectrum format selection** — read the device configuration before the first spectrum and use the upstream format-0 default when `SpecFormatVersion` is absent. Treat unsupported encodings and malformed spectra as failures instead of publishing an empty or incomplete histogram. Covers a regression vector that previously produced `unsupported vlen=6`.
- **Spectrum dashboard example** — replace the time-based ApexCharts example with a Plotly card using a numeric energy axis. Copy-paste YAML is also provided in `examples/spectrum-card.yaml`.
- **Bluetooth response integrity** — require complete, correctly framed replies; release failed connections on timeout, cancellation, malformed replies or write failures. The command deadline includes writes, and callbacks from retired connections cannot corrupt a new session.
- **Spectrum update overhead** — cache totals and attributes per spectrum snapshot and skip repeated state writes between spectrum reads while preserving availability transitions.
- **HA device registry warning** — use a lookup scoped to the integration's config entry on HA 2026.8+, with compatibility for older HA versions.

### Changed
- **Lower device workload** — settings, device-health diagnostics and the temperature register are read at most once per minute during normal polling. HA setting writes still trigger an immediate settings refresh.
- **Spectrum recovery** — preserve the last complete histogram on failure and back off automatic retries from 5 minutes to 1 hour to avoid repeated heavy transfers. Spectrum polling can still be disabled with interval 0.
- **Diagnostics** — include spectrum format, last spectrum error, channel count and time until the next automatic read.

### Validation limits
- Regression tests cover protocol decoding, repeated BLE commands, failures/cancellation, coordinator scheduling and spectrum entity updates. A prolonged RC-103G FW 4.14 hardware test and confirmation of the reported device error remain outstanding; this release does not claim to diagnose that uncaptured error.

---

## [2.0.0b1] — 2026-07-05

First 2.0 beta: **gamma spectrum support**.

### Added
- **Spectrum sensor** — state is the total count across all channels; attributes carry the full 1024-channel histogram (`channels`), the channel→keV energy calibration (`calibration_a0/a1/a2`, E = a0 + a1·ch + a2·ch²), accumulation `duration_s`, and a `truncated` flag. The `channels` attribute is excluded from the recorder so the HA database is unaffected. See the README for a copy-paste ApexCharts card that renders the spectrum as an energy plot.
- **`radiacode.get_spectrum` action** — on-demand spectrum read with response data (current or accumulated via `accumulated: true`), for scripts, automations, and template charts.
- **Spectrum Reset button** — clears the current spectrum accumulation on the device.
- **Spectrum poll interval option** — default 60 s, configurable up to 3600 s; set to 0 to disable spectrum polling entirely. The spectrum is the largest BLE transfer the integration performs.
- Protocol: spectrum decoders for both wire formats (v0 raw uint32, v1 run-length/delta), the format version parsed from the device configuration text, `WR_VIRT_STRING` command for spectrum reset.

### Known limitations (beta)
- Through an ESPHome BT proxy the spectrum transfer may be truncated by the proxy's notification buffer. Truncated spectra decode cleanly up to the cut (leading channels, where most background counts live) and are flagged `truncated: true`. Direct Bluetooth adapters receive full spectra.

---

## [1.3.0] — 2026-07-05

Stable release of the 1.3.0 beta cycle, corrected against RC-103 FW 4.14 hardware results.

### Added
- **Device-health sensors** (batched VSFR read once per minute):
  - **SiPM Bias Voltage** (mV) — verified reading ~26,963 mV (~27 V); drift indicates SiPM aging
  - **MCU Temperature** (°C) — *disabled by default*; hardware-verified as centi-degrees, now correctly scaled (÷100 → e.g. 29.7 °C)
  - **MCU Vref** (mV) — *disabled by default*; verified ~2,093 mV
- **SFR register directory** — the device's self-describing register listing is read once after connect, logged, and included in the diagnostics download. Note: RC-103 FW 4.14 returns it empty over BLE; the field is null in that case.

### Fixed (vs. 1.3.0b1/b2)
- **MCU Temperature scaling** — raw register is centi-°C; was displayed unscaled (e.g. 2971 °C).
- **Accelerometer X/Y/Z removed** — FW rejects the ACC registers over BLE (always Unknown); the entities are removed and cleaned from the registry on upgrade.
- **Empty SFR directory** no longer logs a misleading "0 bytes, 0 entries" INFO line.

---

## [1.3.0b2] — 2026-07-05

### Added
- **SFR register directory** — the device self-describes every Special Function Register it supports (address, size, type, signedness) via the `SFR_FILE` virtual string. The integration now reads this once after connecting, logs a one-line summary at INFO (full listing at debug level), and includes it in the diagnostics download under `device.sfr_register_directory`. This is the authoritative per-firmware source for verifying the register formats used by the 1.3.0b1 device-health sensors. Through a BT proxy the listing may be truncated by the notification buffer limit — a partial listing is kept.

---

## [1.3.0b1] — 2026-07-05

### Added
- **Device-health sensors** — six new diagnostic sensors read from documented (but previously unexercised over BLE) VSFR registers, batched once per minute:
  - **SiPM Bias Voltage** (mV) — detector bias; drift indicates SiPM aging
  - **MCU Temperature** (°C) — *disabled by default*
  - **MCU Vref** (mV) — *disabled by default*
  - **Accelerometer X/Y/Z** (raw) — device orientation; *disabled by default*

  The uncertain entities start disabled because these registers have never been validated over BLE (scaling for MCU temp/Vref and accelerometer axes is undocumented) — enable them from the entity settings to help verify. Registers the firmware rejects show as Unknown; the health batch failing never affects the radiation sensors.
- Device-health readings included in the downloadable diagnostics dump.

---

## [1.2.0] — 2026-07-05

### Added
- **Hardness sensor** — the dimensionless spectral hardness coefficient shown by the Radiacode mobile app: dose rate (µR/h) ÷ count rate (cps). Characterises which energies dominate the spectrum independent of intensity; each isotope has a characteristic hardness, enabling pseudo-identification of sources from HA history graphs. Computed from the same (outlier-filtered) dose/count values exposed to HA, so all three sensors stay mutually consistent.

---

## [1.1.0] — 2026-07-05

Stable release — identical integration code to [1.1.0b1], promoted after validation on RC-103 hardware (FW 4.14).

### Added
- **Dose rate outlier suppression** — corrupt one-off readings from truncated BT-proxy transfers (e.g. 40,000 µSv/h at background) are rejected or held for one-poll confirmation; genuine sustained radiation events pass through with at most one poll of delay. Applies to count rate too.
- **Radiation Alarm is now a 3-state enum sensor** — **No Alarm / L1 Alarm / L2 Alarm** with state-dependent icons, replacing the Safe/Unsafe binary sensor. ⚠️ Automations referencing `binary_sensor.*_radiation_alarm` must switch to `sensor.*_radiation_alarm`.

### Fixed
- **Signal Strength intermittency** — last observed RSSI is held while the BLE connection is active (a connected peripheral cannot advertise) plus a 15-minute grace window when disconnected.
- **Graph gaps** — dose/count rate fall back to the last known value when a poll decodes no records.
- **Log spam** — routine BT-proxy truncation messages downgraded to debug.

---

## [1.1.0b1] — 2026-07-05

### Added
- **Dose rate outlier suppression** — Truncated BLE transfers through ESPHome BT proxies could occasionally produce a misparsed record with an absurd value (e.g. 40,000 µSv/h at background), ruining graph scaling. Two integrity-preserving layers now guard against this: records with non-finite/negative values are rejected at decode time, and a reading that jumps more than 50× above baseline is held back for **one poll** — if the next poll confirms it (genuine radiation events are sustained), it passes through. Real events are never hidden; worst case they appear one poll (~5 s) later. Suppressed outliers are logged as warnings. The same protection applies to count rate.
- **Radiation Alarm is now a 3-state enum sensor** — displays **No Alarm / L1 Alarm / L2 Alarm** instead of the previous Safe/Unsafe binary sensor, with a state-dependent icon. The orphaned binary_sensor registry entry is removed automatically on upgrade. Automations using the old `binary_sensor.*_radiation_alarm` entity must be updated to the new `sensor.*_radiation_alarm` states.

### Fixed
- **Signal Strength intermittency** — the sensor read "Unknown" whenever the HA scanner's advertisement history expired, which is the norm while the BLE connection is active (a connected BLE peripheral stops advertising). The sensor now also checks non-connectable scanner history and holds the last observed RSSI while the connection is active (plus a 15-minute grace window when disconnected).
- **Graph gaps on record-less polls** — dose rate and count rate now fall back to the last known value when a poll's transfer contained no decodable records (previously only zero values fell back, not missing ones).
- **Log spam** — the "Partial response … using partial data" and "VS data truncated" messages fired on a large share of polls through BT proxies (documented, expected behaviour) at WARNING level, flooding the HA log every few minutes. Both are now DEBUG.

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

[Unreleased]: https://github.com/303Bryan/ha-radiacode/compare/v2.0.2rc1...HEAD
[2.0.2rc1]: https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.2rc1
[2.0.1]: https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.1
[2.0.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.0
[2.0.0b1]: https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.0b1
[1.3.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v1.3.0
[1.3.0b2]: https://github.com/303Bryan/ha-radiacode/releases/tag/v1.3.0b2
[1.3.0b1]: https://github.com/303Bryan/ha-radiacode/releases/tag/v1.3.0b1
[1.2.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v1.2.0
[1.1.0]: https://github.com/303Bryan/ha-radiacode/releases/tag/v1.1.0
[1.1.0b1]: https://github.com/303Bryan/ha-radiacode/releases/tag/v1.1.0b1
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
