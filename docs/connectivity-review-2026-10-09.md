# Connectivity and update review — October 9, 2026

This review informs the unpublished 2.0.2rc2 candidate. It separates confirmed observations, code defects, and unresolved hardware/protocol questions. Tests with controlled transports establish software behavior; they do not establish physical proxy reliability.

## What the supplied evidence establishes

The supplied diagnostic export identifies integration **2.0.1**, Home Assistant **2026.10.0**, Python **3.14.6**, device firmware **4.14**, and default options: five-second primary polls and sixty-second automatic spectrum reads. Live GitHub release/branch metadata confirms 2.0.1 remains the latest published stable release. The existing 2.0.2rc1 draft branch has not been installed in this export.

The activity CSV contains 53,490 rows from October 8, 00:00:04 through October 9, 20:13:28 Mountain time, approximately 44 hours 13 minutes. It includes fifteen device controls, Radiation Alarm and BLE Connected. **Dose/count histories and raw DATA_BUF payloads are absent.** The availability observations therefore directly describe the exported controls/alarm, not every radiation sensor.

| Observation | Finding | Interpretation |
| --- | --- | --- |
| Controls/alarm availability | Each has 1,659 aligned unavailable episodes, approximately 13h15m / 30.0% of the window; median 24.95s, maximum 117.45s | Frequent widespread availability flapping is confirmed. One final episode is censored at export end. |
| Reported BLE state | 171 off episodes, approximately 20m11s / 0.76%; median 5.22s, maximum 35.14s | Reported link loss accounts for a much smaller part of the window. This is integration state, not an independent radio capture. |
| Overlap | 1,597 unavailable episodes never overlap a recorded BLE-off spell | Most exported unavailability cannot be equated with a reported disconnected link. Initial BLE state is unknown for about five minutes; after it is known, about 98.8% of unavailable duration occurs with BLE reported on. |
| Recent commands | All twenty retained commands complete successfully on connection generation 26; DATA_BUF replies include all declared bytes | The latest snapshot shows responsive transport, including during a sample gap. |
| Recent radiation phases | Six stale failures grow from 15.2s to 41.0s, followed by normal fresh readings | At least this gap occurs while full reads succeed. The export does not identify whether decoding, timestamp selection, device history or filtering rejected the samples. |
| Spectrum | Last failed read received 937 of 1,217 body bytes; previous full 1,024-channel snapshot retained | A separate incomplete transport transfer is confirmed. The missing bytes cannot be reconstructed by waiting longer. |
| Connection count | 24 successful initializations; client generation 26 | There have also been genuine recovery attempts. Counters are not a count of all outages across the CSV window. |

The user's subsequent Bluetooth dashboard screenshot confirms the RadiaCode is connected to **AIR-1: Garage**, with **one of three connection slots occupied**. Other displayed routes include M5Stack Atom proxies and other Apollo devices. The earlier statement about Wi-Fi M5Stack devices identifies available proxies, not the active route in this snapshot. Slot exhaustion is not supported by the screenshot. Proxy ESPHome/build versions remain unknown.

The diagnostic's −84 dBm value belongs to advertisement metadata remembered at connection time. It can be stale or originate from a different scanner than the active connection, so it is not a confirmed current AIR-1 link measurement.

## Full history review

Reviewed all **84 locally available commits**, including merged main history and candidate branches; live GitHub branch heads match the local known tips. The history repeatedly addresses different failure layers:

| Historical change | What should be retained |
| --- | --- |
| Persistent connections (`3c96b85`, early versions) | Avoid expensive reconnect/initialization every five seconds and accumulated DATA_BUF backlog. |
| Serialized commands and explicit register reads (`307adfd`, `34a9eb2`) | Controls and polls must not share an overlapping response stream. Unsupported batch registers must not corrupt other decoded values. |
| Immediate user disconnect and modern callbacks (`03da038`, `c266bd2`) | Release the device for the mobile app and wake commands when the link drops. |
| Optional settings/health/temperature cadence and strict frame validation (`2c0dedf`, 2.0.0) | Reduce primary work; do not accept incomplete notification streams or reuse an uncertain stream. |
| Primary publication before maintenance, paired timestamps and freshness (`ccbf062`, 2.0.1) | Publishing is responsive, and old cached radiation no longer masquerades as fresh forever. However, controls incorrectly inherited this radiation failure state. |
| Alarm event lengths and complete spectrum groups (`53f8dd8`, `8ddeb52`) | Preserve captured protocol fixes and strict histogram completeness. |
| Spectrum circuit breaker, bounded cached radiation, cancellation cleanup (`291cd76`, `13e08b9`, unpublished RC1) | Limit optional reconnection loops and expire radiation independently while recovery is blocked. |

The project specification describes an earlier design; it is not a current protocol authority. The 2.0.1 freshness gate made missing/rejected samples visible, but inherited `CoordinatorEntity.available` also disabled the settings and reset buttons even when their BLE transport was still live. The current evidence establishes that availability defect. It does not prove that the detector itself froze or that a particular parser branch caused the sample gaps.

## Documentation and protocol findings

1. **One framed, serialized request remains required.** The [upstream Python Bluetooth transport](https://github.com/cdump/radiacode/blob/master/src/radiacode/transports/bluetooth.py) uses eighteen-byte writes without response and length-prefixed notifications. A negotiated MTU of 220 does not establish that the detector accepts larger request chunks. Neither reviewed clients nor manufacturer documentation provides a supported paged spectrum read or a tunable flow-control handshake.
2. **Do not guess a new live-record length.** The [upstream DATA_BUF decoder](https://github.com/cdump/radiacode/blob/master/src/radiacode/decoders/databuf.py) matches the integration's twenty-two-byte RealTimeData record, twenty-three-byte DoseRateDB/UserData/ScheduleData records, and twenty-one-byte RareData. A 127-byte response body leaves 115 record bytes after the echo/virtual-string headers; those could be five database records. This arithmetic does not establish an extra live-record byte. Selection by newest device timestamp remains unchanged pending captured evidence.
3. **The existing connection timeout was not a bound.** [Connector 4.7.1 source](https://github.com/Bluetooth-Devices/bleak-retry-connector/blob/v4.7.1/src/bleak_retry_connector/__init__.py) calls `connect(timeout=20)` inside a separate sixty-second safety window and counts transient errors separately from ordinary attempt limits. Passing `timeout=15` through constructor kwargs does not establish a fifteen-second operation deadline. Older connector 3.0.0 has the same important distinction.
4. **Routing and service caching already exist.** [Home Assistant Bluetooth guidance](https://developers.home-assistant.io/docs/core/bluetooth/) and the [HA Bluetooth wrapper](https://github.com/Bluetooth-Devices/habluetooth/blob/main/src/habluetooth/wrappers.py) support selecting a usable backend at connect time. Connector 4.7.1 accepts but does not invoke `ble_device_callback`; adding it alone is not a routing fix. The cached-client compatibility class provides connector cache-error handling; its `set_cached_services` compatibility method is a no-op on modern Bleak. There is no claim that switching class makes ordinary reconnects faster.
5. **Optional work must not reset primary publication.** [HA fetching guidance](https://developers.home-assistant.io/docs/integration_fetching_data/) and the [2026.10.0 coordinator](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/helpers/update_coordinator.py) support keeping slow work separate. `async_set_updated_data()` changes scheduling and success status; maintenance continues to update caches/listeners without that call.
6. **Proxy transport pressure remains plausible.** [ESPHome proxy guidance](https://esphome.io/components/bluetooth_proxy/) recommends default scan settings, ESP-IDF and good placement, and explains the shared Wi-Fi/Bluetooth radio constraint. Its [Native API documentation](https://esphome.io/components/api/) describes bounded send queues and disconnection when full. Earlier repository research includes an observed notification-forwarding warning; the current files do not contain proxy logs to confirm the dropping layer now. Increase buffers only as a measured, reversible proxy experiment with sufficient memory; no proxy configuration change is included here.

The supplied Home Assistant release pins Bleak 3.0.2, connector 4.7.1 and habluetooth 7.1.2 in its [Bluetooth manifest](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/bluetooth/manifest.json). Its [ESPHome manifest](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/esphome/manifest.json) pins aioesphomeapi 46.6.0 and bleak-esphome 4.0.0. The validation matrix now includes both 2026.10.0 and the integration's minimum supported 2026.9.4 runtime.

## Candidate changes and practical limits

- **Controls follow BLE independently.** Known settings and reset actions stay usable while radiation freshness fails. Setting controls require a known register value; user-disabled BLE, link loss and shutdown disable device controls. The connection switch remains available to reconnect. Settings can be available before the first radiation sample.
- **Establishment has an outer thirty-second deadline.** Keep the partially constructed cached client reachable for cancellation-safe cleanup, including a proxy connection that never returns from establishment. This bounds connector retries; initialization commands, disconnect cleanup and the coordinator's separate retry can make a complete recovery cycle longer.
- **Capture the reason for missing measurements.** Store decoder stop boundaries, emitted record counts, encountered `eid:gid` group counts (including known skipped groups), valid/invalid candidate counts and timestamp ranges, and the last twenty acceptance/rejection decisions with UTC receipt times and connection counts. Distinguish no paired sample, no timestamp, replay, timestamp regression and suppressed outlier. Store no new raw packet dump or identifier.
- **Keep RC1's bounded recovery behavior.** Pause automatic spectra after three failures, retain the last complete spectrum, allow progressing bulk replies up to thirty seconds while rejecting two-second stalls, and retain an accepted radiation pair for at least sixty seconds or three poll intervals. Replays/empty/filtered responses do not renew this lease.

Extending the lease reduces false availability flapping; it is not evidence of fresh measurement production. The CSV includes spells longer than the new lease, so a blanket claim that sixty seconds fixes all gaps would be unsupported. Controls decoupling corrects their availability even after radiation expires; radiation remains unavailable when genuinely stale. Device/protocol reasons for those sample gaps remain open until the new summaries are captured.

## Controlled acceptance on AIR-1: Garage

Install the candidate for testing, record its version, and retain a rollback to 2.0.1. Keep the detector on the same confirmed AIR-1 route and the same poll/spectrum options for the first comparison. Record both the proxy's project version and ESPHome build version; they are different values. No proxy firmware or YAML change is authorized or performed by this candidate.

1. Capture at least one full previous failure window, preferably a 24-hour soak. Count radiation lease expiry separately from BLE-off events, connected-control availability, and spectrum failures. Cached radiation attributes must show its age and expire at the bounded threshold.
2. Export diagnostics soon after another sample gap. Inspect `runtime.data_buf.recent_samples`, candidate type/timestamp ranges, decoder stop reason and `runtime.freshness.last_sample_rejection`. Complete commands plus `repeated_timestamp`, `regressed_timestamp` or a decoder stop identify the next investigation without reconnecting solely because of an incomplete reading.
3. Repeat manual current-spectrum requests with proxy logs captured at matching command times. Require complete 1,024-channel publication or safe rejection with the previous spectrum retained, and bounded recovery to advancing primary samples. Three consecutive automatic failures must pause spectra; a successful manual current-spectrum read must resume them.
4. Compare a second run with automatic spectrum interval set to zero, changing only that option. This distinguishes bulk-transfer interference from sample gaps that happen without spectra. Restore the desired interval afterward.
5. Exercise BLE OFF/ON, integration reload and shutdown. OFF must immediately invalidate radiation/controls and release the peripheral; ON must not revive expired data. Verify no orphaned maintenance task or occupied proxy slot remains after teardown.

A controlled closer-placement or direct-adapter comparison can follow if proxy logs show forwarding loss. Keep reception, firmware and buffer experiments separate so improvement is attributable. Do not promote to stable solely on unit tests or a brief successful connection.
