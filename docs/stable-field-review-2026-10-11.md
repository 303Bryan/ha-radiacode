# Stable field review — October 11, 2026

## Release scope

Version `2026.10.3` promotes the runtime implementation published as `v2.0.2rc5` (commit `3b88e9f72856b760af8237579f1b30550241c06d`). Its only integration-file difference is the manifest version. The current main-branch calendar naming policy and documentation are included. Existing config entries, entity IDs and saved options are preserved.

The reviewed behavior supports promotion of the live-measurement, recovery, dose-counter and spectrum-status improvements. Incomplete bulk transfers remain a known transport limitation on the observed proxy routes; this release does not claim uninterrupted Bluetooth connectivity or repaired incoming notification loss.

## Evidence reviewed

- Three user-supplied state-history exports for count rate, dose rate and hardness, plus the corresponding October 11 graphs and earlier debug trace.
- User-supplied October 11 debug log, ending at 21:13:25.942 UTC / 15:13:25.942 MDT. The timestamped file spans 14:52:41.542–15:13:25.942 MDT, approximately 20 minutes 44 seconds around startup and integration reloads. It is not an additional overnight soak.
- The user confirmed the history interruptions around 14:47–14:58 MDT were associated with updates/reloads.
- Previous diagnostics identify Home Assistant 2026.10.1, detector firmware 4.14, five-second polling and an AIR-1 proxy running ESPHome 2026.9.1. The latest log does not independently establish a new complete environment inventory or installed manifest version.

## Primary measurement behavior

The history exports show approximately 17 hours without a recorded `unavailable` state before the confirmed maintenance period. Values continued changing approximately every five seconds. Hardness is calculated from the accepted paired dose/count readings; its smoother history is not independent proof of another sensor's behavior.

The final debug capture contains 161 structured `DATA_BUF` command summaries, all successful. It records 153 radiation-ready events: 152 fresh accepted measurements and one correctly retained cached sample aged 13.708 seconds. Every selected measurement is `RealTimeData`. Excluding the explicit user disconnect/reload, the largest gap between fresh accepted samples is 21.684 seconds, within the 60-second minimum lease. No RadiaCode freshness-expiry or stale-measurement error is recorded.

An earlier CSV spike was traced to selection of a raw record at 0.9238 µSv/h while the same response contained a live reading near 0.1268 µSv/h. RC3's live-record preference and separate stream watermarks correct that demonstrated selection defect. The integration introduces no new averaging or clamping of ordinary readings. Rare short excursions remain in the history; without a matching debug trace, their record source and physical cause cannot be established.

## Transport and spectrum behavior

Ten distinct incomplete bulk failures are visible: five configuration and five spectrum attempts. Eight have structured command summaries; two precede the full debug capture. Failures occur with both acknowledged and unacknowledged command writes. Failed connections are retired, incomplete replies are rejected, and primary polling recovers.

Two complete 1,024-channel spectra are logged at 14:58:33.862 and 15:06:19.094 MDT. Subsequent failures preserve the last complete snapshot. No current uninterrupted run reaches three consecutive spectrum failures in this capture; the automatic pause threshold and metadata publication are covered by regression and actual Home Assistant runtime tests. Reloading resets the failure series, so repeated reloads can permit further bounded acquisition attempts.

The final run uses confirmed writes (`with_response`). The planned longer comparison with confirmed writes off is not established by this file. The option remains off by default, and promotion preserves existing saved choices rather than silently changing them.

Requested Bluetooth sources in this capture vary from the previously identified Garage AIR-1. Source lookup/request records include a Kitchen proxy and later a Basement AIR-1, with intermittent weak lookup signal. The active-connection source is not independently reported, so these records do not prove the actual radio route or the cause of lost fragments. Do not attribute every failure in this capture to the Garage AIR-1.

## Release validation

The local full suite passed with 443 tests and four skipped modules requiring the actual Home Assistant runtime. An independent focused review also passed 213 release-sensitive tests and found no blocking runtime or migration defect. GitHub CI separately validates the exact release candidate with generic tests, HACS, hassfest and Home Assistant 2026.9.4, 2026.10.0 and 2026.10.1 runtime jobs.

Release acceptance covers responsive primary updates, conservative frame rejection, bounded recovery, retained spectrum snapshots and compatible saved state. Reliable repeated bulk acquisition across all proxy/device combinations and the longer confirmed-writes-off comparison remain follow-up field validation. Check Spectrum `acquired_at` and `automatic_paused` when deciding whether a displayed histogram is current.
