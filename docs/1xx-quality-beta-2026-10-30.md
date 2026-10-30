# 1XX measurement quality beta — 2026.10.4b1

This prerelease builds on stable 2026.10.3 for testing on RC-101/102/103/103G/110. It adds measurement uncertainty, calculated quality and alarm status, schema-driven event decoding, and firmware-change handling. It does not add ZERO support, flash firmware, change dose conversions, or establish a Bluetooth-proxy reliability fix.

## Source evidence

The October 27 [manufacturer changelog](https://radiacode.com/changelog) and the supplied Windows 1.16.01 revision history describe 1XX firmware 4.15 quality indicators, temperature alarms, and the displayed-unit change from roentgen to rem. The bundled 1XX manual, page 26, gives the 30% dose-rate / 50% count-rate uncertainty limits and temperature criterion; pages 31–33 describe thermal alarms and acknowledgment behavior.

The Windows package's embedded default configuration remains identical to Windows 1.16.00 and Android 1.66.03. PE resource 15419 is 3,401 bytes with SHA-256 `406eaee6ff7511d11a05bacfd0abaa2d20bad9b12e04fcc965f0d5c8009d9b19`. It declares optional event channels through the expression `raw_mask * 8 + 7`. The beta reads the actual detector configuration and accepts only known channel types/scales and a validated fixed prefix. All combined-mask tests use synthetic data, not captured firmware 4.15 alarm records.

Installer SHA-256: `8fd1a88636f05d9913f8422d05378c24997800ae76bc42eb9e4eb71fbd7a7b24`. Actual Windows executable SHA-256: `5bd939ce2ebeb79d7977050fbea1b97e588a1d50cba59205da48b42724733cf4`. The native executable is packed/protected; no executed Windows BLE timeout algorithm or new alarm-bit map was recovered.

## Expected behavior

| Item | Expected behavior |
|------|-------------------|
| Dose Rate / Count Rate | Existing values and IDs continue; attributes add uncertainty and calculated quality |
| Quality sensors | Normal/reduced, or HA unknown when evidence is insufficient; unavailable when radiation freshness expires |
| Detector Temperature Status | Calculated low/normal/high; unknown after 180 seconds without an actual temperature observation |
| Count Rate Alarm | Calculated L1/L2 comparison in CPS using device thresholds converted from counts/10 seconds |
| Accumulated Dose Alarm | Calculated L1/L2 comparison in µSv using the resettable dose; expires after 180 seconds without an advancing dose record |
| Radiation Alarm | Existing ID and dose-rate comparison retained, with calculated source now explicit |
| Device events | Up to ten recent decoded events in diagnostics, with raw code/flags and channel values in declared native units |
| Firmware reconnect | Rechecks firmware, then refreshes dependent caches on change; optional failures remain bounded |
| Spectrum | Existing complete-frame validation, retry limits and acquisition timestamp remain; a confirmed firmware change discards the pre-update snapshot |

Quality never uses MCU temperature. Replayed, rejected, or filtered measurements cannot replace the accepted sample's error percentages. Cached or failed temperature reads cannot renew its observation time. Event-only and counter-only buffers cannot renew radiation freshness.

The reported quality is calculated, not decoded firmware alarm state. The manufacturer descriptions differ at exactly 45 °C; the beta reports unknown quality at that boundary. Below −20 °C it also cannot establish normal quality. Native alarm codes, latched states and acknowledgment bits remain opaque. A calculated alarm is not a complete mirror of the device alarm UI.

## Testing the beta

1. Install `v2026.10.4b1` with prereleases enabled in HACS, or use the attached manual-install ZIP. Restart Home Assistant. Confirm the integration version and the detector's firmware version in diagnostics.
2. During ordinary background measurements, compare dose rate and count rate with the detector/app. Check that their new `uncertainty_percent` values track the device's available error readouts. Verify that normal/reduced/unknown quality has the expected reason, particularly during startup or a new averaging interval.
3. Check Detector Temperature Status against the existing detector Temperature sensor. Its age should restart on an actual new observation. MCU Temperature is a different reading.
4. Check calculated alarm threshold attributes against existing device settings. Preserve the current settings for comparison. Temperature/alarm boundary cases are covered with synthetic fixtures; ordinary use is sufficient for the initial hardware check.
5. Toggle the integration's BLE Connection switch off and back on. Radiation/quality availability should recover normally, firmware checking should occur in optional maintenance, and a same-firmware reconnect should not repeatedly download configuration.
6. If the detector is already on firmware 4.15, collect diagnostics after reconnect and after any naturally occurring event. Check that decoded events do not prevent later radiation updates. If firmware is updated separately with manufacturer software, confirm the HA firmware version and refreshed configuration after reconnect.
7. Leave ordinary logging/history running for a longer session through the usual Bluetooth path. Check sample continuity and spectrum `acquired_at` / `automatic_paused`; old spectrum snapshots are not evidence of successful current acquisition.

For a report, include integration/HA/firmware versions, detector model, direct Bluetooth versus proxy path, the approximate event time, downloadable diagnostics, and a bounded debug log covering the problem. Useful fields include `transport.configuration.firmware_check_pending`, `schema_usable`, `event_schema`, `group4_schema`, `runtime.freshness`, `runtime.temperature_status`, `runtime.device_events`, and `runtime.data_buf.stop_reason`.

## Acceptance boundary

The beta includes pure-protocol, fake-transport/coordinator, and real Home Assistant runtime tests. They verify code behavior and state publication. Physical firmware 4.15 behavior, new alarm codes, and a prolonged Bluetooth-proxy soak remain to be verified before stable promotion. No numerical dose-unit change is inferred from the display-label change to rem.
