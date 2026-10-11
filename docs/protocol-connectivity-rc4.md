# RC4 protocol and connectivity evidence

Prepared October 10, 2026 for 2.0.2rc4. The source packages were inspected statically; neither application nor firmware was executed. Physical AIR-1 (ESPHome 2026.9.1) behavior must be measured separately from regression and Home Assistant runtime results.

## Confirmed source evidence

Android package `com.almacode.radiacode` 1.66.03, version code 16605, SHA-256 `d4a170cdb054eb88c82e584b8454ecfb400028d2417300aed82aa082b1c72cec` provides compiled BLE and record-decoder references. Windows installer SHA-256 `b76c4232990558be002db378e9b5a3484d905bed5c54b47a22ec5bb533d287c1` contains desktop app 1.16.00 (May 21, 2026), manuals and matching firmware payloads. Its native code appears protected; Windows timeouts/retries were not recovered.

The Windows app embeds a 3,401-byte configuration identical to the APK asset: RT_RCDATA ID 15419, file offset 0x5DB9F8, RVA 0x5EBFF8; raw SHA-256 `406eaee6ff7511d11a05bacfd0abaa2d20bad9b12e04fcc965f0d5c8009d9b19`. Both define group 4 DoseCounter as unsigned 32-bit micro-Roentgen plus unsigned 16-bit flags. With the seven-byte scalar header, each captured record is 13 bytes. Five RC2 field-log records contained 106009, 106029, 106039, 106049, 106059 micro-Roentgen and flags 0x3000; RC3 incorrectly required 16 payload bytes.

Android help `Help/en/dose.htm` identifies this as an independent non-resettable counter. The compiled app routes RareData into the current accumulated-dose value and DoseCounterData into separate history state. RC4 converts the integer counter by dividing by 100 to micro-Sieverts, exposes **Dose Counter**, and preserves **Accumulated Dose** as the resettable RareData value. It uses Home Assistant's TOTAL state class with no last_reset for the independent counter, following [sensor guidance](https://developers.home-assistant.io/docs/core/entity/sensor/).

The BLE app uses 18-byte command chunks, length-prefixed complete responses, echoed command/modulo-32 sequence validation, and acknowledged writes on Android API 33+. SET_EXCHANGE supplies a target ID and unsigned 16-bit detector TX/RX sizes. RC4 keeps the existing chunk size/deadlines, adds split-header assembly and bounds replies by detector TX size. The optional write setting selects an explicit [Bleak response mode](https://bleak.readthedocs.io/en/latest/api/client.html#bleak.BleakClient.write_gatt_char); it cannot recover missing incoming notification fragments.

The Windows manual, PDF page 7 / printed page 4, says log entries are deleted after being read. The RC-10x device manual, PDF page 13 / printed page 11, describes newly generated connected-session data being forwarded without storage in autonomous memory. These are documented behavior, not proof of the exact DATA_BUF consumption/acknowledgment point. RC4 does not replay a failed command on the same connection or promise history recovery.

## Configuration and failure boundaries

Read the actual detector configuration once and cache its recognized group 4 layout for the same device across reconnects. Recognized older UserData retains its 16-byte layout; unknown schemas stop at group 4 without guessing from remaining bytes. Reload after firmware changes. Configuration also supplies the spectrum format, avoiding a redundant format-only read when recognized. Complete configuration failures cannot establish a schema; incomplete transport responses retire the connection. Failed discovery backs off for five minutes across reconnects, then retries as optional maintenance after primary readings have been published.

Transfer limits belong to a connection and are reset before negotiation/recovery. Counter values remain separate from the live radiation timestamp, filters and freshness lease. **Confirm Bluetooth writes (experimental)** defaults off, remains reversible through options/reload, and refuses unsupported write characteristics before sending an init command. A timeout after a write does not trigger an unacknowledged replay.

## AIR-1 acceptance plan

1. Install RC4 through HACS beta releases, restart Home Assistant, and confirm diagnostics/version. Keep ESPHome and radio placement unchanged.
2. Leave confirmed writes off initially. Capture a comparable operating period with live dose/count updates, counter records, ordinary polling and spectrum outcomes. Compare incomplete frames, unavailable duration, reconnects and command latency against RC3.
3. Enable confirmed writes in integration options. Repeat the same conditions, checking diagnostics for selected mode and negotiated limits. Disable it if initialization/polling stalls or connection failures increase.
4. Confirm Dose Counter remains independent after Reset Accumulated Dose, and that counter-only traffic does not keep stale dose/count readings available.
5. Exercise optional spectrum acquisition and sustained idle monitoring. Retained spectra and samples must disclose staleness; hardware gaps remain possible after notification loss.

A beta release and successful CI establish code behavior. Promotion to stable requires the sustained device/proxy observations above.
