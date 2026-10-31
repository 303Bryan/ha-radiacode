# 1XX stability review and beta 4 acceptance

## What the earlier builds established

| Build | Keep | Remaining limitation |
|---|---|---|
| Stable 2026.10.3 | Complete-frame validation, one command at a time, stale-client rejection, bounded connection/command/cleanup deadlines, accepted-pair freshness, cached spectrum and three-failure spectrum pause | Some bulk transfers still failed through proxies; stable evidence did not establish compatibility with all later record layouts |
| 2026.10.4b1 | Separate uncertainty/quality statuses, calculated alarms with freshness, raw event evidence, confirmed-firmware invalidation | Configuration in initialization and repeated register-directory downloads increased reconnection work; firmware 4.15 records stopped at byte 22 |
| 2026.10.4b2 | Primary publication before configuration maintenance, configuration backoff, one register-directory attempt per load/firmware | Configuration still retried indefinitely at five-minute intervals; in-memory schema disappeared on reload |
| 2026.10.4b3 | Three-attempt configuration budget survives reconnects, including cancellation; explicit diagnostics for paused discovery | Failed small measurement transfers still cause reconnects. No complete configuration or spectrum was obtained in the latest capture |
| 2026.10.4b4 | Validated declared scalar layouts and private, identity/firmware-checked persistent configuration | Requires one successful configuration download; the affected detector's actual 4.15 declaration and physical soak remain unverified |

The vendor Windows 1.16.01 and Apple 1.0.17 build 5 applications contain matching 1XX firmware 4.15 images. Static Apple-app analysis supports configuration-driven decoding using DType, RType, Expr, P1 and P2, 18-byte command chunks, and retiring desynchronized exchanges. It does not establish a new proxy timeout or prove acknowledged writes work better through ESPHome. The inspected four-second wait belongs to RSSI polling. Preserve the current transport defaults and never automatically resend uncertain writes.

## Latest supplied capture

The October 31 export includes older unrelated messages, but structured RadiaCode command summaries run from approximately 11:57:27 to 12:23:36 local time. This is about 26 minutes of detailed evidence, not an overnight soak. Connection-generation resets and repeated initial identity reads indicate integration reloads near the beginning.

| Operation | Complete responses | Incomplete responses | No response |
|---|---:|---:|---:|
| Measurement buffer | 227 | 17 | 1 |
| Configuration | 0 | 4 | 0 |
| Spectrum | 0 | 2 | 1 |
| Register directory | 0 | 1 | 0 |

The four configuration failures span reloads: one occurs before the final load's three-attempt budget. They do not demonstrate a broken cap. The final configuration pause occurs about 12:09:30 and the spectrum pause about 12:16:05. Measurement buffers still fail at 12:16:22 and 12:16:56. The final connection then continues through the end of the capture.

There are 207 logged fresh paired readings from 11:59:23 to 12:23:36; the longest gap between these accepted readings is 37.674 seconds. The attached diagnostics report a fresh reading less than one second old, no loaded configuration, configuration attempts paused at three, and automatic spectrum polling paused at three failures. No spectrum acquisition time is available. General polling is five seconds and the configured spectrum interval is ten seconds.

All 204 observed sequence mismatches at byte 22 have a zero byte followed by the expected sequence. This corroborates the earlier boundary evidence, but does not identify which field changed. A two-byte realtime flag is a supported declaration in beta 4, not an assumption applied to firmware 4.15. No resynchronization-by-byte-skipping or relaxed framing has been added.

The display name of an advertisement source is not proof of the active BLE connection route. The earlier Kitchen-route observation cannot establish which proxy carried this later capture. No HA options or proxy firmware were changed for this implementation.

## Implemented behavior

The scalar decoder supports the established identities and scaling for realtime, raw, dose-rate database and rare groups. Channel order follows the configuration. It rejects contradictory IDs, duplicate keys/groups, unsupported wire/representation types, units or expressions, excessive structures and truncated records. An unsupported explicitly declared group cannot publish partial values. Legacy decoding remains for absent scalar declarations; existing event/group-4 gates remain in place.

The cache requires supported realtime and rare groups, a complete transfer, and a confirmed 1XX serial/firmware identity. It stores the text in private HA storage with parser version and SHA-256, and revalidates before applying it. Cached metadata waits for firmware verification on reconnect. Invalid or mismatched stored data is discarded and bounded device discovery remains available. Storage is not a source of measurement freshness and does not authorize ZERO support.

The synthetic fixture `tests/fixtures/scalar-1xx.txt` explicitly models known scalar contracts. The wider flag and reordered-channel tests are synthetic variations. They prove parser behavior, not the actual detector's layout. Real HA runtime tests exercise atomic storage, reload activation, identity mismatch and confirmed firmware invalidation.

Home Assistant's public [Store helper](https://github.com/home-assistant/core/blob/2026.10.4/homeassistant/helpers/storage.py) provides asynchronous loading/saving and private, atomic file support. Cache work runs in optional maintenance rather than adding BLE startup prerequisites.

## Field acceptance before stable

1. Install 2026.10.4b4 through HACS beta releases and restart/reload as usual. Keep the detector powered and stationary near the tested proxy, with vendor apps disconnected. Keep existing settings constant for the initial comparison. This work does not change HA options remotely.
2. Inspect `transport.configuration`: a successful read should show `loaded: true`, `source: device`, validated scalar groups and a populated layout including RType. The cache also needs a confirmed 1XX identity and supported realtime/rare groups. If downloads pause again, preserve diagnostics; repeated reloads only restart the failure budget.
3. After one successful read, reload once. Confirm `source: storage`, continued scalar decoding and no repeat configuration download. Battery, temperature and accumulated-dose updates must come from correctly decoded complete records; unavailable values are not proof of a valid new layout.
4. Capture several hours of accepted-reading continuity and command outcomes, including a controlled disconnect/reconnect and one reload. Check freshness expiry during a deliberately interrupted connection, then recovery without stale readings presented as new. Record configuration/spectrum pauses separately from measurement-buffer failures.
5. If bulk transfers remain incomplete, obtain a complete configuration and buffer capture through a controlled direct adapter connection, releasing the HA connection first. Do not change acknowledged writes and proxy settings simultaneously. An acknowledged-write comparison is a separate experiment, not a default change in this beta.
6. Promote only after the exact candidate passes CI, the actual 4.15 declaration and following records are verified, and the field run supports the claimed behavior. Missing bulk notifications, absent schema or a short recovered segment do not establish stable hardware behavior.

ZERO support, guessed alarm bits/units, timeout increases, automatic write-mode changes and physical proxy fixes are outside this candidate.
