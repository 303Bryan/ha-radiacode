# Protocol and stability research

Reviewed October 3, 2026 before the revision following 2.0.0. This document records evidence, unresolved protocol questions, and the reason for the acquisition architecture. It is not a claim of physical device validation.

## Scope and sources

The review covered every integration Python module, all tests, services, configuration and translations, workflows, README, changelog, original project specification, relevant Git history, and all repository issues and pull requests. It also compared the manufacturer documentation, independent clients, maintained spectrum decoders, Home Assistant's coordinator, and ESPHome's relay implementation.

The original `PROJECT_SPEC.md` describes an earlier proposed implementation. Its disconnect-per-poll lifecycle and some command identifiers conflict with subsequent accepted code and observed wire messages. The implemented protocol and the evidence below take precedence for understanding current behavior.

| Source | What it establishes |
| --- | --- |
| [Radiacode hardware](https://www.radiacode.com/100-series?lang=en), [manual](https://downloads.radiacode.com/EN/RC-10x_Device_Manual.pdf) | Scintillator/photomultiplier signal processing, onboard measurement and LCD, and the different record-storage path while connected to an application. |
| [Manufacturer spectrum documentation](https://radiacode.com/docs/en/100-series/software/android/spectrum/spectrum-channels), [changelog](https://radiacode.com/changelog/?lang=en) | Firmware 4+ uses 1,024 channels. The published 4.14 fix concerns dose calculation; it does not identify the reported uncaptured error or an LCD freeze fix. |
| [cdump client](https://github.com/cdump/radiacode/blob/2b217916f49f5eedddf8f0d9116fcfd3c6b8832e/src/radiacode/radiacode.py), [transport](https://github.com/cdump/radiacode/blob/2b217916f49f5eedddf8f0d9116fcfd3c6b8832e/src/radiacode/transports/bluetooth.py) | Initialization and length-prefixed BLE framing, 18-byte request chunks, writes without response, command serialization, and the conventional configuration lookup. |
| [mkgeiger client](https://github.com/mkgeiger/RadiaCode), [Qt client](https://github.com/petriska/qtradiacode/tree/e71f13e23a01c2ba5ca6ab5167de06d88e2d741b), [radiacode-tools](https://github.com/ckuethe/radiacode-tools) | Independent transport comparisons and separate acquisition workers. None supplies a supported paged spectrum/configuration request. Their assumptions and size limits require review rather than wholesale adoption. |
| [Steaeavean fork](https://github.com/Steaeavean/radiacode_stuff) | Bleak experiments and a reported RC-101/FW4.14 soak. Its rare encoding branches were not captured in that soak, and its vlen4 padding workaround was subsequently withdrawn elsewhere. |
| [BecqMoni decoder](https://github.com/Am6er/BecqMoni/blob/11c4235f060873b910d7e5f36275e933f21939da/BecquerelMonitor/RadiaCodeIn.cs#L1511), [releases](https://github.com/Am6er/BecqMoni/releases) | Newer reverse-engineering evidence: vlen5 is absolute uint32; signed deltas wrap modulo 2^32; vlen4 uses three bytes per value without an extra padding byte. This is not a manufacturer-certified protocol specification. |
| [HA coordinator](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/helpers/update_coordinator.py), [fetching-data guidance](https://developers.home-assistant.io/docs/integration_fetching_data/) | Entities are notified after the awaited update returns. `async_set_updated_data()` also resets the polling schedule. Optional acquisition therefore needs its own managed task and listener publication. |
| [ESPHome notification relay](https://github.com/esphome/esphome/blob/2d56559f04d43095467c4a37c46ad5a97e471f18/esphome/components/bluetooth_connection/bluetooth_connection_hub.cpp#L360), [proxy guidance](https://esphome.io/components/bluetooth_proxy/) | A full API TCP buffer can cause notification loss without replay. BLE event-queue pressure can also lose events. Shared Wi-Fi/Bluetooth radio use and reception matter independently of the decoder. |

## Captured failures

The supplied October 3 log and matching diagnostics identify integration 2.0.0, Home Assistant 2026.9.4, device firmware 4.14, and default five-second radiation / sixty-second spectrum intervals.

Both log entries labeled `Spectrum read failed` were actually reads of CONFIGURATION (virtual string `0x02`). Each expected a 3,268-byte response body, but only 1,948 or 596 bytes arrived. There was no read of SPECTRUM (`0x200`) in that capture. Format discovery was preventing the histogram request, so the empty chart was not evidence of a chart-rendering defect.

A July trace contains a stronger transport finding: a spectrum response expected 1,254 body bytes, received 754, and still received the final short fragment. Twenty-five whole 20-byte packets were absent from the middle. Partial bytes cannot safely be treated as contiguous leading channels. The trace's logged packet prefixes are incomplete, so they cannot be represented as an exact full spectrum fixture.

Normal DATA_BUF responses were mostly quick: median 0.286 seconds across 106 successful replies. The longest complete coordinator cycle took about 31 seconds during initialization/recovery/optional acquisition. Radiation was obtained before optional operations, but HA publication still awaited them. Two complete empty replies followed reconnect/draining; they represent absent measurements, not zero radiation.

One disconnect was followed by missing advertisements before recovery. This is distinct from decoding and can make immediate retries fail despite a previously visible device. Prior [issue #9](https://github.com/303Bryan/ha-radiacode/issues/9) also contains a direct-adapter versus proxy comparison. [Issue #21](https://github.com/303Bryan/ha-radiacode/issues/21) remains hardware-validation evidence, not a confirmed decoder-only root cause.

Live inspection identified a stock proxy release 26.8.2 built with ESPHome 2026.7.4. That was the latest published stock proxy release at review time, even though newer ESPHome source existed. Project version and ESPHome build version must be recorded separately when investigating transport problems. Advertisement RSSI may come from a different proxy than the active connection and becomes stale while the device stops advertising.

## Architecture and protocol decisions

- Keep one serialized physical BLE command and persistent links. History commit `3c96b85` adopted persistent links after repeated 7–15 second proxy initialization allowed DATA_BUF to accumulate; short sessions can reproduce that backlog.
- Publish primary radiation after DATA_BUF decoding. Run due identity, temperature, settings, health and spectrum acquisition in one managed maintenance task. Cancel and await it during disconnect, unload and shutdown.
- Optional publication updates the latest cached snapshot and listeners without marking radiation fresh, clearing a primary failure, or resetting its schedule.
- Select dose and count together from the latest valid measurement. Zero is a valid measurement; `None` means absent. Track sample timestamp, record type, flags, receipt age and repeated timestamps separately.
- Reject incomplete transport frames and retire the stream. Continuations have no independent command framing, and increasing the timeout cannot restore dropped packets.
- Read the spectrum directly and validate both known encodings. Accept only a complete, uniquely valid 1,024-channel interpretation, or an authoritative format where needed. A raw-format payload is 4,112 bytes, but compressed format can also have that size; length alone is not sufficient.
- Publish successful current-spectrum service reads to the same entity cache. Accumulated-spectrum requests remain separate snapshots.

## Unresolved device questions

The captured real-time flags changed from `0x0040` to `0x4040` after a disconnect, and measurement uncertainty restarted at a larger value. Device timestamp offsets then advanced about 190.5 seconds over 279 seconds of host wall time. This records an acquisition/state change, but does not establish whether the cause was a firmware state, queued samples, or processor slowdown.

The public clients convert a signed timestamp offset in ten-millisecond units relative to the initialization time anchor. The meaning of flag `0x4000` and the trailing real-time byte remains unverified. Preserve these raw fields rather than inventing a semantic label. A possible alternate gid4 record layout in another client also remains unverified for this device; it is not grounds to silently change record sizes.

The SET_EXCHANGE payload `01 ff 12 ff` is shared by reference clients but its flow-control semantics are undocumented. Do not tune its bytes as a proxy fix without independent protocol evidence. No supported partial/paged configuration or spectrum retrieval was found.

## Verification and future captures

Pure decoder and fake-transport tests establish framing, known encodings, zero readings, paired samples, and lifecycle behavior. Real Home Assistant tests must additionally establish that a blocked maintenance read does not delay the already acquired radiation state, optional updates preserve primary failure/freshness, and shutdown leaves no maintenance task running.

The candidate requires Home Assistant 2026.9.4 or newer, matching the validated runtime and supplied installation. The previous HACS minimum of 2024.1.0 was incompatible with the coordinator config-entry argument and managed background-task API used here.

Physical acceptance requires repeated complete 1,024-channel snapshots through the actual proxy, a populated chart, responsive device readings/LCD, timely HA readings, and a soak beyond the previously problematic connected duration. Test results from synthetic transports alone cannot establish these outcomes.

## RC1 hardware follow-up

The installed RC1 made direct SPECTRUM requests through the user's proxy, but two observed transfers were incomplete. One expected 1,010 body bytes and received 336; another expected 1,009, received 929, and included the final 13-byte fragment. The latter lost four complete 20-byte notifications from the middle. This confirms that bypassing configuration acquisition did not repair the relay path. Keep the candidate unpromoted while investigating the transport.

The added stop-reason logs also exposed an independent Event alignment defect. A complete captured Event record is `5d00077b0c0000140340110b67a0413100`: sequence 93, group 7, event 20, channel mask 3, flags 0x1140, then a float count rate and uint16 error. That six-byte tail was omitted by the inherited four-byte Event parser. The header boundary is corroborated by the decoder's expected sequence 93. The fixture uses this exact event followed by a synthetic measurement; it is not a captured whole response.

[Upstream issue 45](https://github.com/cdump/radiacode/issues/45) reproduces Android-decoded count alarms with mask 3 and dose alarms with mask 12 and lists their configuration fields. RC2 consumes the known six-byte tails for those masks, retains legacy mask 0, and stops on other masks. The general eight-bit field mapping remains unverified.

The user reports sluggish CPS numerals with responsive buttons/menus. The manufacturer specifies a [0.5-second LCD period](https://radiacode.com/docs/en/100-series/device/tech-specs), but [Monitor mode](https://radiacode.com/docs/en/100-series/display-modes/monitor-mode) averages stable measurements. Compare the [Search graph](https://radiacode.com/docs/en/100-series/display-modes/search-mode) at 0.5 seconds per bar with HA connected and disconnected before treating a slowly changing numeral as proof of stalled measurement or display processing. No reviewed source establishes that DATA_BUF reads or time initialization reset LCD averaging.

Capture bounded command/target histories with sequence, link generation, declared/received bytes, fragment count/sizes, first-response latency, largest gap, total and lock-wait duration, timeout classification and disconnect reason. Keep measurement type/time/raw flags and fresh-sample age separate from request success. Keep spectrum format provenance and retry/snapshot ages. Routine debug output should summarize these values without repeatedly printing the histogram or full device configuration.

### Close-range proxy comparison and empty spectrum groups

With RC1 still installed, a current-spectrum read eight inches from the stock ESP32 proxy failed with 416 of 1,027 body bytes received. A subsequent comparison beside an Apollo AIR-1 (ESP32-C3, ESPHome 2026.9.0) confirmed that proxy as the active connection source at approximately -24 dBm. Its first manual read received 891 of 1,031 body bytes, including the final 15-byte fragment: seven middle 20-byte notifications were missing. Strong signal and different proxy hardware therefore did not eliminate transport loss. These observations do not identify the specific dropping layer.

The installed stock proxy's [ESPHome 2026.7.4 notification handler](https://github.com/esphome/esphome/blob/2026.7.4/esphome/components/bluetooth_proxy/bluetooth_connection.cpp#L477) ignores a failed API send, and the [API send path](https://github.com/esphome/esphome/blob/2026.7.4/esphome/components/api/api_connection.cpp#L2149) can reject a frame when TCP buffering is full. This is a source-supported hypothesis, not a captured proxy-side drop in these tests. AIR-1's local web log did not expose a matching drop message during the next read.

That next read received all 1,036 body bytes in 52 twenty-byte notifications in 0.503 seconds. Its 1,024-byte virtual payload was nevertheless rejected by our decoder. The captured 32-byte prefix is `74190000a6958d40db03184034f5b43900000000510011081e7df44201464108`: after the normal sixteen-byte duration/calibration header are two zero-count groups, then a five-channel uint8 group with counts 17, 8, 30, 125 and 244. The remaining 992 payload bytes were not retained in the bounded debug prefix, so this is not a captured full-histogram fixture.

The [cdump spectrum decoder](https://github.com/cdump/radiacode/blob/2b217916f49f5eedddf8f0d9116fcfd3c6b8832e/src/radiacode/spectrum.py), [BecqMoni](https://github.com/Am6er/BecqMoni/blob/11c4235f060873b910d7e5f36275e933f21939da/BecquerelMonitor/RadiaCodeIn.cs#L1521) and [Android decoder](https://github.com/darkmatter2222/Open-RadiaCode-Android/blob/2c8fd97d7f72c71dfab80e3b2b65367e1510e2c7/android_app/app/src/main/java/com/radiacode/ble/RadiacodeBleClient.kt#L433) all consume zero-count groups without adding channels. RC3 follows that behavior, with guaranteed two-byte progress and the same requirement to consume the entire payload and produce exactly 1,024 channels. It adds neither a reserved header nor an initial seed. Tests using the captured prefix explicitly identify their synthetic remainder. Physical acceptance remains pending.

## Final production validation for v2.0.1

RC3 hardware checks confirmed complete 1,024-channel spectrum publication and a populated Home Assistant chart. An independently audited HA snapshot contained 109,241 counts, matching its channel sum, with `truncated: false`. Live observations also showed the total advance from 108,290 to 109,241. Calibration and plotted energies were consistent with the published attributes. These are published histogram/rendering observations; the complete compressed bytes for that snapshot were not retained for replay. The earlier RC notes above describe the investigation at each stage.

The user reported a steady Search-mode display during the observed session. After an incomplete spectrum transfer, fresh radiation returned 6.674 seconds after the logged failure, with a 16.897-second gap between fresh samples. Another 21 fresh samples followed over 104 seconds; subsequent intervals had a 5.057-second median. Connection retirement, reconnect and resumed primary polling were confirmed, while a bulk transfer could still delay an acquisition.

A separate read on an independently confirmed ESPHome 2026.9.0 proxy declared 1,068 body bytes, received 968 and lacked 100. During that same SPECTRUM request, the proxy logged `Failed to send notify data response, handle 0x0010`. This directly establishes a notification-forwarding failure; its TCP-buffer cause was not separately logged. [ESPHome's warning implementation](https://github.com/esphome/esphome/pull/18605) explains that warnings can be suppressed after the first failure, so warning count is not lost-notification count. The last complete spectrum remained cached.

The proposed `network.tcp_send_buffer: 16kB` proxy candidate compiled but was never installed; it is not included in this integration release. The original proxy YAML was restored. The user independently undertook an ESPHome 2026.9.1 update; these proxy observations remain tied to 2026.9.0, and no 2026.9.1 hardware result is claimed. Repeated spectrum-forwarding reliability, prolonged-connection stability and resolution of the uncaptured device error remain unverified.
