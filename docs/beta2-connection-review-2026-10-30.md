# Beta 2 connection findings and testing

The supplied October 30 field capture runs integration **2026.10.4b1**, Home Assistant **2026.10.4**, and RC-103 firmware **4.15**, using Bluetooth proxies and unacknowledged writes. This review separates the integration's retry regression from incomplete Bluetooth replies and incomplete record decoding. Raw logs, device identifiers and the original diagnostic export are not included in the repository.

## Confirmed findings

1. **A reconnect loop introduced by beta 1.** Between 13:42 and 13:48 local time, seven consecutive `SFR_FILE` downloads failed with incomplete replies. Each failure retired its connection. The new reconnect firmware check then retried the same 4,615-byte framed diagnostic reply about a minute later. The eighth directory read succeeded at 13:49:56. Firmware identity must be rechecked without repeatedly requesting this optional directory.
2. **Bulk configuration can prevent startup.** Failed configuration downloads appeared as initialization failures at 13:40 and 13:42. This inherited behavior was still present in beta 1. Configuration discovery belongs in optional maintenance, after primary publication, with its existing five-minute failure cooldown.
3. **Incomplete spectrum responses remain.** The spectrum circuit breaker paused automatic reads at 14:25:54 after three consecutive failures. The retained histogram was acquired at 14:09:33 local time. A later fresh dose/count reading does not make that spectrum current. Disabling automatic spectrum polling removes this optional source of connection disruption; it does not repair missing notifications.
4. **Firmware 4.15 record decoding needs further evidence.** The detailed capture contains 1,006 decoder stops on sequence mismatches: 1,002 at byte 22 and four at byte 37. The unread prefix begins with an extra zero before the apparent next sequence/header. This is consistent with a changed record width, but the log contains only a short prefix, not the actual live-record declaration or full payload. No width is inferred from those bytes. The snapshot reports `group4_schema: null`, and battery, accumulated dose and dose counter are absent. These issues are separate from BLE frame completeness.
5. **Later radiation polling continued.** The detailed capture contains 1,008 accepted fresh radiation pairs; the largest interval between those accepted pairs is about 12.2 seconds. This does not cover the earlier startup outage. At the diagnostic snapshot, sample age is about 3.2 seconds and the last poll succeeded. Following the 14:25:56 reconnect, no further connection retirement appears before the log ends at 15:07:28. This is a bounded observation, not proof of a prolonged stable connection.

## What 2026.10.4b2 changes

- Firmware/serial identity and the diagnostic register directory are separate maintenance operations. The directory gets one automatic attempt per integration load or confirmed firmware change. An error or cancellation consumes that attempt; reconnect and the BLE Connection switch do not re-arm it. A known successful or empty directory is retained until firmware changes/reload.
- Configuration discovery is removed from connection initialization. It runs in background maintenance, retains its failure cooldown across reconnects, and never accepts an incomplete response.
- Diagnostics now include `transport.configuration.record_layout`, a bounded structural summary from the device's actual configuration. It exports known group/channel names, numeric IDs, data types, scales and recognized units; arbitrary text and identity are excluded. This summary is evidence only and does not select or guess new layouts.
- `runtime.maintenance.register_directory_attempted` and `runtime.maintenance.errors.register_directory` make the one-attempt behavior visible.
- Runtime CI includes Home Assistant 2026.10.4 as well as 2026.9.4, 2026.10.0 and 2026.10.1.

## Test sequence

1. Install **v2026.10.4b2** with prereleases enabled in HACS and restart Home Assistant. Confirm the installed version in diagnostics.
2. For the initial comparison, set **Spectrum poll interval to 0** in the integration options. Keep the existing Bluetooth path and write mode constant. Dose/count polling continues; the manual spectrum action remains available. Changing options reloads the integration and permits one new directory attempt.
3. Let primary polling run. A directory failure may cause one reconnect, but must not repeat every minute. Check that firmware still appears correctly and accepted measurements advance. On a later BLE Connection off/on cycle, the failed directory should remain attempted while firmware is rechecked.
4. Download fresh diagnostics after configuration discovery succeeds (`transport.configuration.loaded: true`). The `record_layout` entries for group 0 and group 4 are needed to resolve the firmware 4.15 record-boundary and counter-schema mismatch. Include the recent `runtime.data_buf` status; do not assume the missing battery/dose entities are a disconnected device.
5. Once the primary-only run is characterized, restore the previous spectrum interval if desired and compare connection behavior. Retained spectra must be assessed by `acquired_at` and `automatic_paused`.

Hardware acceptance of beta 2 remains pending. This release does not claim to fix proxy notification loss, firmware 4.15 record decoding, or native alarm acknowledgment/latched flags. ZERO support remains deferred.
