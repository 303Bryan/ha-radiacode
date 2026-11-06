# Sparse dose and automatic spectrum recovery: 2026.11.2b3

## Field evidence

The private beta-2 capture spans November 5, approximately 06:35–18:50 MST (12 hours 15 minutes). It confirms integration 2026.11.2b2, Home Assistant 2026.11.0, and detector firmware 4.15. The user moved the detector between the Kitchen and Garage proxies and confirmed automatic spectrum settings were unchanged and enabled. The capture begins mid-run, so earlier pause decisions are outside its window. Raw captures and identifiers are excluded from this repository.

| Observation | Result |
| --- | --- |
| Accepted fresh paired readings | 8,787 |
| Longest gap between accepted pairs | 26.860 seconds; none reached the 60-second live lease |
| Failed DATA_BUF transfers | Four, followed by automatic reconnect recovery |
| Dose-bearing observations | 41 |
| Longest gap between dose observations | 50 minutes 1.355 seconds |
| Dose gaps exceeding beta 2's 30-minute limit | Nine |
| Spectrum attempts | Zero |

Applying the beta-2 dose-age rule predicts approximately two hours of accumulated-dose unavailability despite fresh rates. This is calculated from the logged observations, not measured HA recorder history. The initial interval is excluded because prior dose age is unknown. The sparse reporting pattern does not establish a guaranteed manufacturer cadence.

The absence of spectrum attempts with automatic collection enabled is consistent with an earlier automatic pause, but the pause event and a contemporaneous diagnostics snapshot are absent. This run therefore cannot validate spectrum reliability or prove why acquisition stopped. Its good live-rate performance also cannot be attributed solely to software or placement: bulk spectrum traffic was absent. Advertisement source and preferred proxy ordering do not establish the active BLE route.

## Beta behavior

Accumulated Dose is treated as the last observed counter state during continuous live monitoring. The separate 30-minute timer is removed; actual observation age is preserved and the counter is never estimated from rates. Both the numeric sensor and calculated alarm require the live paired-sample lease. A genuine lease expiry or user disconnect invalidates pre-interruption dose evidence. New rates alone cannot revive that old total; a new counter observation, successful register read, or confirmed reset is required. Startup remains unknown until actual dose evidence arrives. The existing three-failure pause for unsupported register reads remains in place.

Spectrum recovery uses the existing bounded serialized transport. After three failures, normal acquisition pauses and one automatic probe is scheduled after at least 30 minutes. Subsequent failed probes back off to at most one hour, without resetting the failure budget on reconnect. Every probe requires 60 seconds of continuous live-sample availability on the current connection. A complete current-spectrum result clears the pause and restores the configured interval. Interval 0 disables all automatic probes. Failed/cancelled attempts cannot publish a new histogram or renew live measurement freshness.

## Automated coverage

- Synthetic dose records over three hours, including 50-minute gaps and a longer retained observation, with advancing primary samples and failed register reads.
- Independent and delayed live-expiry callbacks, replayed dose records, short reconnects, user disconnects, and a register read crossing an outage.
- A synthetic spectrum run with retry intervals of 5, 10, 30, 60, and 60 minutes, repeated retired links, eventual successful recovery, and resumed normal cadence.
- Probe stability gating, cancellation, disabled automatic acquisition, manual current/accumulated actions, and retained snapshot age.
- Actual HA entity publication for dose continuity and failed/successful automatic probes. CI retains 2026.9.4, 2026.10.0, 2026.10.1, and 2026.10.4, adding 2026.11.0 with official dependency pins.

These checks use controlled BLE results; they do not establish physical proxy throughput.

## Physical acceptance for this beta

1. Install 2026.11.2b3 and restart HA. Keep the detector in the same location, retain the same polling/spectrum settings, and enable debug logging for the overnight comparison.
2. Allow the first actual dose observation to arrive. Confirm Accumulated Dose and its alarm then stay available through gaps longer than 30 minutes while live rates continue, with observation age increasing honestly.
3. Confirm Spectrum `acquired_at` advances during ordinary acquisition. If failures occur, collect diagnostics showing `automatic_recovery_pending` and `retry_in_seconds`; after the cooldown and a healthy live stream, verify a new attempt and eventual complete spectrum without a manual reload/reset.
4. Export debug logs and diagnostics before reloading. Compare accepted-sample gaps, actual dose availability, spectrum successes/failures, recovery attempts, and reconnects. An optional deliberate live outage should make rates unavailable and require new dose evidence afterward; account for the resulting sparse-record wait.

Physical acceptance remains pending. Keep this build a prerelease until a run exercises active spectrum traffic and confirms both entity behaviors.
