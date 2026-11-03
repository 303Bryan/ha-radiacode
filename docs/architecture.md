# Architecture

[Documentation index](README.md) · [Contributing](../CONTRIBUTING.md)

Radiacode is a local-polling Home Assistant custom integration. All runtime files live under `custom_components/radiacode/`; the embedded BLE implementation is shipped with the integration.

## Responsibilities

| Module | Responsibility |
| --- | --- |
| `config_flow.py` | Bluetooth discovery, manual address normalization, duplicate prevention, and polling/write-mode options |
| `__init__.py` | Config-entry lifecycle, platform forwarding, registry migrations, private-cache removal, and spectrum action registration |
| `coordinator.py` | Primary polling, sample acceptance and expiry, bounded recovery, optional maintenance, and device metadata |
| `radiacode_ble/client.py` | HA-compatible Bleak transport, serialized commands, response framing, connection teardown, and configuration cache validation |
| `radiacode_ble/protocol.py` | Binary framing, records, settings, spectrum decoding, and validated configuration layouts |
| `radiacode_ble/firmware_profiles.py` | Bundled declarations for exact known model/version/application-build combinations |
| Entity platform modules | Sensor, binary sensor, switch, number, select, and button behavior |
| `diagnostics.py` | Bounded diagnostic snapshots with device and proxy identifiers redacted |
| `services.yaml`, `strings.json`, `translations/en.json` | Action schema and user-facing strings |

## Lifecycle and Bluetooth

Setup forwards platforms without waiting for a device connection. Entities can start unavailable; coordinator polling begins when entities subscribe. This prevents an unreachable detector or proxy from delaying Home Assistant startup.

The BLE connection **stays open between polls**. Use Home Assistant's Bluetooth device lookup and the Bleak retry connector so local adapters and ESPHome proxies remain supported. Do not replace this with a separate scanner or a Linux-only transport.

Commands share a serialized transport. Responses must have complete valid framing; bytes from a failed or retired connection must not become the next command's reply. Ordinary commands have a ten-second response deadline. Bulk responses have up to thirty seconds once packets arrive, and a two-second packet stall fails the transfer. Connection establishment has its own thirty-second deadline; initialization, a coordinator retry, and cleanup have separate bounds. These are not one total polling deadline.

Turning off BLE Connection cancels maintenance and releases the link. Unload and Home Assistant shutdown also tear down timers, tasks, and the connection. Keep these paths responsive even when a transfer is stalled.

## Measurement publication

Primary acquisition publishes dose/count values as a pair from one accepted sample. It prefers valid live records; raw/database records are fallbacks. Replayed or regressed timestamps, empty buffers, and suppressed outliers do not renew freshness. Device-relative timestamps and HA receipt time serve different purposes.

An independent timer expires radiation availability after `max(60 seconds, 3 × poll interval)`. Cached readings can bridge a brief reconnect but cannot renew themselves. User-disabled Bluetooth makes measurements unavailable immediately. Temperature observations expire after 180 seconds. Accumulated Dose and its alarm share a separate, bounded 30-minute observation limit, selected to cover the approximately 28-minute maximum dose-record gap in the beta-1 field capture. Both also require the live paired-rate lease. Live traffic never changes the dose observation timestamp: these entities report the last observed total, not a continuously sampled or integrated estimate.

Advancing RareData defers a fallback individual dose-register read for 60 seconds; a successful unchanged register value is a new observation. This register is not readable on all firmware. Three consecutive failures pause its automatic reads until reload or a confirmed firmware/build change; reconnects, dose resets, and RareData do not reset that budget. A status-only rejection is never decoded as zero. A successful reset establishes a bounded zero observation, and an older in-flight read cannot replace it. Dose observations never renew the live paired-rate lease. Independent timers publish both dose-age and live-lease expiry.

A reading more than 50× above baseline is held for confirmation. Changing this policy, unit conversions, or sample selection requires targeted regression coverage and field evidence.

Calculated quality and alarm states are Home Assistant interpretations. Opaque device flags/events must not be labeled as acknowledged, latched, or silenced alarms without protocol evidence. Controls use link/settings availability independently from live radiation freshness.

## Optional work and spectra

Settings, identity, temperature, accumulated dose, device diagnostics, configuration, register-directory, and spectrum operations run as bounded maintenance around primary acquisition. Preserve primary-poll priority. Automatic spectrum reads additionally wait for an accepted live sample on the current connection, so a reconnect can restore measurements before another bulk transfer.

Incomplete spectra are rejected. The previous complete histogram and acquisition timestamp remain available; failed transfers do not create a fresh spectrum. Automatic spectrum acquisition backs off and pauses after three consecutive failures. A successful manual current-spectrum read clears the pause; an accumulated-spectrum read does not replace the current snapshot. A configured spectrum interval of zero still disables automatic acquisition.

Configuration discovery is limited to three automatic attempts with a retry interval; reconnects do not reset this budget. Register-directory discovery has a separate attempt guard. See [troubleshooting](troubleshooting.md) for recovery behavior.

## Firmware declarations and private storage

Complete, validated device configuration is authoritative. Bundled profiles apply only to exact known model, version, and application-build identities. A version number alone cannot select a new record width or unit. Unknown or malformed declared layouts stop decoding rather than guessing offsets.

Firmware is rechecked after every reconnect. Successful identity checks are cached only for that connection; the 60-second retry cooldown applies to failures and survives reconnects. Confirmed firmware/build changes invalidate dependent metadata. Persisted configuration requires matching device identity, firmware/build, parser version, and integrity checks. Home Assistant `Store` owns the private cache; the integration deletes it when its config entry is removed. Storage failure must not interrupt ordinary BLE polling.

Diagnostics retain allowlisted configuration-source and profile labels while redacting identifiers and arbitrary names. Keep identity-bearing device captures private; commit only reviewed, sanitized fixtures with provenance. Public vendor configuration fixtures are distinct from private device captures.

## Compatibility and validation

The supported HA floor is declared in [hacs.json](../hacs.json). The [validation workflow](../.github/workflows/validate.yml) is the source of truth for Python versions and the pinned HA runtime matrix.

Lightweight tests use controlled dependencies; runtime tests exercise actual HA APIs with controlled BLE replies. Neither establishes physical proxy throughput or hardware acceptance. Keep synthetic fixtures, captured records, and field observations explicitly distinguished. See [contribution checks](../CONTRIBUTING.md) and the [field-report index](README.md).
