# Beta 3: bound configuration retries

The longer October 30 capture exported at 16:09:59 Mountain time includes the earlier run as well as the later stationary test near a kitchen Bluetooth proxy. The user reported moving, powering and restarting the detector during the earlier period; the exact restart time is not established by the log. Earlier outages are therefore not a clean stationary comparison. Raw logs and device identifiers are not included in this repository.

## Later segment: 15:41:31–16:09:56

- All **344 DATA_BUF commands completed successfully**. The coordinator accepted **336 fresh dose/count pairs**, with a maximum interval of **14.965 seconds**. A successful transport command is not necessarily a new measurement; initialization drains and empty/repeated replies do not renew freshness.
- **Five configuration downloads failed**, at 15:46:32, 15:51:33, 15:56:37, 16:01:38 and 16:06:39. All five connection retirements in this segment were caused by those incomplete replies. The integration retried this optional work every five minutes without a maximum attempt count.
- Configuration frames declared 4,275 body bytes. The five replies were missing 60, 659, 300, 160 and 160 bytes respectively. Incomplete frames were correctly rejected; retaining this validation is necessary because late continuations cannot safely be reused as another command's response.
- The preferred advertised proxy path had RSSI around −63 to −69 dBm in the later segment, substantially stronger than earlier observations. The active-source diagnostic was unavailable, so the requested/preferred path is not independent confirmation of the actual active proxy.
- **No spectrum commands** occurred anywhere in this capture. The register-directory download was attempted only once, at 15:24:21, despite subsequent reconnects. Beta 2's directory retry guard behaved as intended in this run.

## Remaining decoder evidence

All nine configuration downloads in the full capture failed; there is no successfully downloaded configuration in this log. In the later segment, 334 DATA_BUF decoder stops still occurred: 333 at byte 22 and one at byte 37, on a sequence mismatch. Complete transport replies can therefore deliver a live pair while later records remain unread. This mismatch is independent of the optional-download retry policy. The device's complete `transport.configuration.record_layout` remains necessary before changing record widths or dose-counter units.

## What beta 3 changes

Automatic configuration discovery gets at most **three unsuccessful attempts**, with the existing five-minute delay between them. An attempt is consumed before awaiting the transfer, so cancellation also consumes it. Reconnect and the BLE Connection switch preserve the budget. After the third failure, discovery pauses and primary polling plus other maintenance continue.

Diagnostics expose `transport.configuration.automatic_attempts`, `automatic_attempt_limit` and `automatic_paused`. While paused, `retry_in_seconds` is null. A complete successful configuration read clears the attempt count and caches its declarations; incomplete replies cannot populate the cache. Reloading the integration, using a different detector address or detecting a confirmed firmware change permits new automatic attempts. The first known firmware value and an unchanged/unknown firmware result do not reset the budget.

## Hardware test

1. Install **2026.10.4b3**, restart Home Assistant, and confirm the version in diagnostics.
2. Keep the detector powered and stationary near the same proxy, with RadiaCode phone/Windows connections closed and **Spectrum poll interval = 0**.
3. Capture at least **20 minutes**. If configuration downloads remain incomplete, up to three attempts are expected during roughly the first ten minutes. After the pause warning, there should be no further automatic configuration downloads or connection retirements caused by them. Do not reload or toggle options during this comparison, since a reload starts a new budget.
4. Export a fresh log and integration diagnostics JSON. Check that fresh dose/count readings continue, `automatic_paused` is true with three attempts when discovery has failed, and no retry is scheduled. If `loaded` is true instead, retain the `record_layout` evidence for the firmware decoder follow-up.

This test has not yet been performed on beta 3. The change does not claim to fix lost notifications, firmware 4.15 decoding or spectrum transfer failures. ZERO support remains deferred.
