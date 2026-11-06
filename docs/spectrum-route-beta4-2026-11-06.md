# Spectrum transfers and connected-route beta: 2026.11.2b4

## Overnight beta-3 evidence

A private RC-103 / firmware 4.15 capture on Home Assistant 2026.11.0 covers approximately 11 hours 59 minutes. Raw logs, identifiers, room names, and diagnostics are excluded from this repository.

- 8,431 accepted live sample pairs; longest receipt gap 41.151 seconds. No observed gap reached the 60-second live-availability lease.
- 29 actual accumulated-dose observations, with gaps up to 46 minutes 59 seconds. End-of-run diagnostics retained the observed total with live continuity. Recorder history was not supplied, so this is coordinator evidence rather than a recorder-level availability audit.
- 35 automatic spectrum attempts: 10 complete, 25 incomplete. Automatic collection recovered after three extended pauses, proving beta 3 can resume; the final snapshot was nevertheless about two hours old.
- The 25 incomplete spectra lacked 40–818 body bytes. Sixteen included a short notification and lacked exact multiples of 20 bytes. Complete reads finished in 0.47–1.14 seconds; failed reads generally stopped receiving bytes and expired on the two-second silence limit. This pattern is consistent with lost notification chunks, including middle chunks, rather than a uniformly slow transfer.
- Seventeen DATA_BUF transfers were also incomplete. This is not solely a spectrum decoder problem. Incomplete streams were retired; subsequent primary polls recovered.

The log does not establish where notification loss occurs. It contains no proxy forwarding warnings; proxy-side logs were not supplied. ESPHome's reviewed notification-forwarding implementation can discard data if API sending fails and logs the first drop per connection, but that implementation is not proof of this run's cause. A short packet likewise is evidence about delivery, not independently proof of a terminal packet or its loss location.

## Proxy identification

A later read-only Home Assistant connection-monitor check confirmed the currently connected proxy. Its private name is omitted here. This observation cannot retroactively attribute every overnight failure to that route. The beta-3 log records preferred/requested advertisement paths, which can differ from HA's selected backend.

Beta 4 captures the connected HA scanner or a recognized ESPHome backend after successful establishment. Missing/changed private metadata fields yield unknown without affecting connection. BLE Connected shows the current source/name; disconnected attributes clear. Each command freezes its route at start, and the last 20 spectrum commands remain available separately from routine primary polls. Session-local route IDs remain visible in redacted diagnostics; they restart on reload and are not persistent identifiers. The route map retains at most eight sources.

## Recovery policy under test

At the default 60-second interval, failures wait 60, 300, 600, then 900 seconds. Further failures remain at 900 seconds; a slower user interval is a floor. Every retry requires a minute of continuous accepted live measurements on the current connection. A reconnect or a real live-stream gap restarts that stability wait. Retry deadlines survive reconnects and BLE toggles. Interval zero remains disabled.

The shorter schedule aims to reduce stale-spectrum duration on an intermittently successful path. It increases recovery opportunities and potentially reconnect frequency compared with beta 3's one-hour cap. It does not claim to repair missing packets. Complete frames remain mandatory; failure retains the previous snapshot and timestamp and retires the affected transport. Primary polling retains priority. Device connection parameters, write mode, detector accumulation, and proxy configuration are unchanged.

## Validation and acceptance

Synthetic tests cover missing middle notifications, retained short-tail evidence, route selection differing from discovery, unsupported backend metadata, clearing live routes, bounded/copy-safe spectrum histories, and preserved source redaction. Timed tests cover first-retry stability, reconnect/user-toggle gating, cancellation, configured-interval floors, automatic recovery, and retained snapshot age. The HA runtime matrix exercises real wrapper routing plus entity publication.

Before stable promotion:

1. Install beta 4, restart Home Assistant, and confirm the reported version. Keep placement, automatic spectrum interval, and proxy settings unchanged for comparison.
2. Compare BLE Connected's route with HA Bluetooth Connections. Capture the route ID and the proxy firmware/project versions privately.
3. Leave automatic spectrum collection enabled. Collect integration debug logs and, if available, logs from the confirmed proxy covering incomplete transfer timestamps.
4. Compare complete/attempted spectra, longest snapshot age, reconnects, and maximum accepted-measurement gaps against beta 3. Recovery must not regress primary availability.
5. Download diagnostics with the separate spectrum history. Confirm accumulated-dose entities continue using actual observations and live continuity. Physical spectrum improvement remains unverified until this run completes.

## References and licensing review

The full README bibliography was reviewed for retained implementation, supported configuration, tests, examples, and contributor tooling. Existing references remain relevant. Added versioned references cover habluetooth's connected-scanner metadata, bleak-esphome's backend source, and ESPHome's notification-forwarding path. Upstream licenses were reviewed (habluetooth Apache-2.0; bleak-esphome MIT); no upstream implementation was copied or adapted. Existing notices remain unchanged.
