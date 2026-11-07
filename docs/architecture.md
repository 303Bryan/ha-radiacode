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

An independent timer expires radiation availability after `max(60 seconds, 3 × poll interval)`. Cached readings can bridge a brief reconnect but cannot renew themselves. User-disabled Bluetooth makes measurements unavailable immediately. Temperature observations expire after 180 seconds. Accumulated Dose and its alarm retain the last observed counter while the live paired-rate lease stays continuous; there is no independent counter-age timeout. Sparse dose records in the long beta-2 capture were up to 50 minutes apart. An expired live lease establishes a monotonic cutoff: observations before that cutoff remain unavailable after rate recovery until new dose evidence arrives. Check the cutoff both in the expiry timer and before processing a new poll, so a delayed timer cannot revive old evidence. Brief reconnects inside the lease preserve continuity; user disconnect invalidates it immediately. Live traffic never changes the dose observation timestamp: these entities report the last observed total, not a continuously sampled or integrated estimate.

Advancing RareData defers a fallback individual dose-register read for 60 seconds; a successful unchanged register value is a new observation. This register is not readable on all firmware. Three consecutive failures pause its automatic reads until reload or a confirmed firmware/build change; reconnects, dose resets, and RareData do not reset that budget. A status-only rejection is never decoded as zero. A successful reset establishes a zero observation subject to live-stream continuity, and an older in-flight read cannot replace it. Dose observations never renew the live paired-rate lease. The live-lease timer independently expires both rate and accumulated-dose availability; temperature retains its separate expiry timer.

A reading more than 50× above baseline is held for confirmation. Changing this policy, unit conversions, or sample selection requires targeted regression coverage and field evidence.

Calculated quality and alarm states are Home Assistant interpretations. Opaque device flags/events must not be labeled as acknowledged, latched, or silenced alarms without protocol evidence. Controls use link/settings availability independently from live radiation freshness.

## Optional work and spectra

Settings, temperature, accumulated dose, device diagnostics, configuration, register-directory, and spectrum operations run as bounded maintenance around primary acquisition. Preserve primary-poll priority. Automatic spectrum reads additionally wait for an accepted live sample on the current connection, so a reconnect can restore measurements before another bulk transfer.

Incomplete spectra are rejected. The previous complete histogram and acquisition timestamp remain available; failed transfers do not create a fresh spectrum. Automatic spectrum acquisition backs off and pauses normal cadence after three consecutive failures. At the default 60-second interval, consecutive failures schedule retries after 60, 300, 600, then 900 seconds, capped at 900 seconds. Slower configured intervals remain a floor. Every retry, including the first, additionally requires 60 seconds of continuous accepted-sample availability on the current connection. Reconnects and user BLE toggles do not clear the retry deadline or failure budget; cancelled automatic transfers consume an attempt. A complete current spectrum clears the pause and publishes immediately. Following any failure, the next automatic read waits at least five minutes or the configured interval, whichever is longer, and requires 60 seconds of live readings on the current connection. A second complete current spectrum at or after that deadline restores normal polling. A failed confirmation starts retries at the same five-minute minimum. An early manual read can refresh the histogram without shortening confirmation. Reading the accumulated spectrum leaves the current snapshot and pacing intact. A configured spectrum interval of zero still disables automatic acquisition.

Configuration discovery is limited to three automatic attempts with a retry interval; reconnects do not reset this budget. Register-directory discovery has a separate attempt guard. See [troubleshooting](troubleshooting.md) for recovery behavior.

## Firmware declarations and private storage

Complete, validated device configuration is authoritative. Bundled profiles apply only to exact known model, version, and application-build identities. A version number alone cannot select a new record width or unit. Unknown or malformed declared layouts stop decoding rather than guessing offsets.

After the mandatory stale-buffer drain, the primary poll verifies firmware before reading the first live buffer on either an initial connection or a retry. If verification fails, live reads wait for the bounded identity retry. Successful identity checks are cached only for that connection; the 60-second retry cooldown applies to failures and survives reconnects. Confirmed firmware/build changes invalidate dependent metadata. Persisted configuration requires matching device identity, firmware/build, parser version, and integrity checks. Home Assistant `Store` owns the private cache; the integration deletes it when its config entry is removed. Storage failure must not interrupt ordinary BLE polling.

Diagnostics retain allowlisted configuration-source and profile labels while redacting identifiers and arbitrary names. Keep identity-bearing device captures private; commit only reviewed, sanitized fixtures with provenance. Public vendor configuration fixtures are distinct from private device captures.

## Compatibility and validation

The supported HA floor is declared in [hacs.json](../hacs.json). The [validation workflow](../.github/workflows/validate.yml) is the source of truth for Python versions and the pinned HA runtime matrix.

Lightweight tests use controlled dependencies; runtime tests exercise actual HA APIs with controlled BLE replies. Neither establishes physical proxy throughput or hardware acceptance. Keep synthetic fixtures, captured records, and field observations explicitly distinguished. See [contribution checks](../CONTRIBUTING.md) and the [field-report index](README.md).

## Connected-route evidence

After connection establishment, the client reads the selected HA wrapper scanner (or the ESPHome backend source when available). These guarded private fields provide observational metadata only; unavailable or changed layouts produce an unknown source and cannot prevent connection. Requested BLEDevice and advertisement sources remain separately labeled. The integration leaves route selection to Home Assistant.

BLE Connected exposes the live source/name and clears them when disconnected. Transport metadata retains the last established route; command summaries freeze the route at command start so teardown cannot erase failure evidence. Session-local numeric route IDs permit comparison after diagnostics redact identities. The mapping retains at most eight sources; a removed source receives a new ID if seen again, and IDs restart on reload. The last 20 spectrum commands have a separate history so frequent primary polls cannot overwrite all bulk-transfer evidence. Histories contain sizes/timings, never partial histograms.
