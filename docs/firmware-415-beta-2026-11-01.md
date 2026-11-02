# Firmware 4.15 decoder beta: 2026.11.1b1

This candidate continues the October 1XX beta series. The November version follows the repository's calendar release policy. ZERO remains a separate update.

## What changes

The vendor's 4.15 configurations declare real-time flags as a 16-bit value, rem-based dose units and a 19-byte group-5 measurement-session payload. Beta 4 could support the wider flag after loading a schema, but rejected the new units and retained a 16-byte group-5 skip. This candidate accepts the validated units and consumes the complete declared measurement layout. Group 5 is skipped as session data; it does not become a live count/dose reading or extend freshness.

Matched vendor builds can now use bundled record declarations without the 4,263-byte RC-103 configuration download. The declarations contain record formats, units and scales, not device settings, alarm thresholds or calibration. Spectrum encoding retains its independent validation. A complete downloaded configuration takes precedence, including when it declares an unsupported layout.

## Profile selection and invalidation

The client already reads the serial number and `GET_VERSION` during background identity maintenance. The latter contains separate bootloader and application tuples. We now read the complete application build string, with strict length and character checks, and match all three fields below:

| Serial model | Application version | Exact application build |
| --- | --- | --- |
| RC-101 | 4.15 | `Oct 15 2026 10:17:27` |
| RC-102 | 4.15 | `Oct 15 2026 10:17:49` |
| RC-103 | 4.15 | `Oct 15 2026 10:18:10` |
| RC-103G | 4.15 | `Oct 15 2026 10:18:33` |
| RC-110 | 4.15 | `Oct 15 2026 10:18:57` |

These strings were extracted from the application images. They are not the containers' packed creation timestamps, which differ. No timezone conversion is applied. A numeric version alone, a bootloader build, a partial reply or a nearby timestamp does not select a profile. The embedded declaration must also pass a SHA-256 integrity check and the same scalar, counter and event validators used for downloaded configuration.

This identifies supported vendor builds; it is not cryptographic verification of a detector's flash contents. Container hashes in diagnostics identify the source artifacts, not checksums queried from hardware. Custom firmware preserving a vendor's model/version/build identifiers is outside these profiles' applicability. The candidate does not issue `FW_IMAGE_GET_INFO`, enter update mode or add any other Bluetooth command. The update-image versus running-image ambiguity in the research therefore does not affect selection.

After reconnect, a retained profile waits for application-version/build confirmation. A changed build invalidates protocol and dependent coordinator metadata even when the version stays 4.15. Unknown builds retain existing bounded configuration discovery. A later complete device read or correctly matched private device cache replaces the profile. Bundled declarations are never persisted as device downloads. Storage parser version 2 includes the application build; old cache envelopes are rejected.

## Source evidence

The offline extraction used the reviewed container decoder from [darkmatter2222/radiacode-firmware at 3a208390](https://github.com/darkmatter2222/radiacode-firmware/blob/3a208390b146f3a0aa3caa80b6cdcd2840bce20c/pipeline/rcfmt.py). It was applied to the vendor files in the supplied macOS Radiacode 1.0.17 application; all five compressed containers matched those extracted from Windows 1.16.01 byte for byte. Independent word-oriented CRC checks passed on all 39 new payload blocks and all eight RC-103 4.14 reference blocks. The decoded reference image also matched the research repository's committed flash image byte for byte.

The six configuration fixtures under `tests/fixtures/firmware/` preserve the extracted declarations. `provenance.json` records source container SHA-256, original CP1251 configuration SHA-256 and the reduced record-layout SHA-256. The production module includes only the common record declaration and five model/build identities. Extraction/research tooling, flash images and device commands are not dependencies of the integration.

The separately labeled RC-102 4.14 capture fixture comes from the repository's [historical capture](https://github.com/darkmatter2222/radiacode-firmware/blob/3a208390b146f3a0aa3caa80b6cdcd2840bce20c/firmware/rc-102/hw1-fw4.14/analysis/capture.json). Its two-tuple version response provides a captured regression for application-build parsing. Its 1,009-byte measurement buffer decodes completely into nine typed records with no unread bytes. All 4.15 measurement streams used in tests are synthetic records constructed from vendor declarations, not new device captures.

## Automated acceptance

Coverage includes all five actual vendor schemas; both real-time flag widths; preserved later battery/dose/counter records; unchanged dose conversions; malformed units/IDs/types/scaling; truncated measurement records; session-only freshness; legacy captured data; exact model/version/build selection; bootloader/target separation; unknown or missing identities; corrupt bundled declarations; reconnect confirmation; complete device-config precedence; and build-aware storage invalidation.

The pinned Home Assistant runtime matrix exercises real Store behavior, profile activation through coordinator identity maintenance without a configuration transfer, and invalidation after a same-version build change. See the candidate commit's validation run for results. Local lightweight tests do not substitute for that runtime matrix or for hardware testing.

## Field acceptance

1. Install `2026.11.1b1` from HACS beta releases and restart Home Assistant as required for the integration update. Keep the detector at the chosen Basement Air-1 location for the comparison, with companion apps disconnected.
2. Once identity maintenance completes, download diagnostics. For the matched RC-103 build, expect `transport.configuration.source: firmware_profile`, `firmware_build: Oct 15 2026 10:18:10`, supported scalar groups `0`, `1`, `2`, `3`, `5`, and a `dose_counter` group-4 layout. A matching profile should make configuration discovery unnecessary. If the build differs, collect the diagnostics; do not substitute a profile manually.
3. Confirm that new complete DATA_BUF responses consume the following records without the repeated byte-22 stop. Check battery, temperature, accumulated dose and dose counter where the device emits them. A connected state alone does not establish fresh count/dose readings.
4. Check a controlled reconnect and HA reload, then a prolonged stationary run. Record actual connection-route evidence where available, fresh-reading gaps, initialization failures, incomplete response counts and proxy API availability. An advertisement source alone does not prove which proxy carried the connection.
5. Inspect spectrum `acquired_at` and `automatic_paused` separately. This change does not reset a stale spectrum into a new measurement or relax transfer validation.

Physical acceptance is pending. The earlier overnight run included discovery, notification-subscription and proxy/API failures while optional downloads were paused. Decoder corrections and reduced configuration traffic cannot establish that those connection failures are repaired. Stable promotion requires new evidence from this exact candidate.
