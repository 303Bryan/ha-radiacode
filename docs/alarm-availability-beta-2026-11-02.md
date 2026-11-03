# Alarm availability beta — 2026-11-02

**Follow-up:** Physical beta-1 testing found the register fallback unsuccessful. The [beta-2 review](alarm-recovery-beta2-2026-11-02.md) supersedes the proposed availability behavior below; this report preserves the original test plan.

## Supplied evidence

The user reports the detector remained stationary near AIR-1. The supplied diagnostics report Home Assistant 2026.10.4, integration 2026.11.1, RC-103 firmware 4.15 (Oct 15 2026 build), and default integration options. No live Home Assistant configuration was changed during this review.

- The accumulated-dose alarm activity export contains 41 `No Alarm`, 41 `unavailable`, and two `unknown` records. Of 40 completed `No Alarm` → `unavailable` intervals, 36 last 180 seconds within 0.1 seconds. Median is 180.001 seconds; the others include interruptions and a longer interval. The screenshot shows the same repeated three-minute pattern.
- The BLE history export contains seven disconnect/reconnect pairs, lasting 2.520–7.908 seconds. This is a separate symptom from the repeated three-minute accumulated-dose expiry. The files do not provide full histories for every measurement sensor.
- At the diagnostic snapshot, BLE is connected, primary polling is successful, and the last accepted paired reading is about 2.6 seconds old. Recent records are complete, advancing RealTimeData samples. Spectrum is complete (1,024 channels), about 47 seconds old, with no automatic pause. Accumulated dose is cached at zero; the snapshot contains no dose observation age, and the latest primary frame has no accumulated-dose timestamp.
- The diagnostic transport records the last disconnect reason as `command:incomplete_response`. Its active connection source is null and other proxy identities are redacted. These files do not establish which proxy carried the active link, nor the cause of each historical disconnect.

The code expires accumulated-dose alarms after 180 seconds without an advancing RareData record. This directly explains the regular alarm expiry pattern without requiring a three-minute BLE outage. The numeric Accumulated Dose entity previously followed live rate freshness, which could leave its cached value visible while its alarm was unavailable.

## Changes in 2026.11.2b1

1. Use Dose Rate Alarm, Count Rate Alarm and Accumulated Dose Alarm consistently; use the same prefixes for their L1/L2 threshold controls. Keep unique IDs, existing entity IDs, state strings and threshold attributes. Keep user-assigned names under user control.
2. After 60 seconds without a new dose record, schedule an individual `RD_VIRT_SFR` read of resettable dose register `0x8022`. The supplied register directory declares it as `RC_VSFR_DS_urem`, unsigned 32-bit; legacy code names it `DS_uR`. Convert native micro-dose units to µSv by dividing by 100. Use individual reads because this register has been rejected by batch reads on supported firmware.
3. A complete successful read renews the dose observation even if the value is unchanged. Read failures do not turn into zero or renew the 180-second deadline. Fresh stream records defer the fallback; failed reads keep a one-minute cooldown. Primary polling retains scheduling priority. The numeric dose sensor and alarm share the same freshness evidence.
4. A successful reset starts a bounded zero observation and requests a register read. An older in-flight register read cannot overwrite that reset. Dose reads never renew live paired-rate freshness and never use the independent non-resettable Dose Counter.
5. Include dose source, age and freshness in attributes and diagnostics for the next test review.

## Device thresholds

These values match both the supplied settings and the bundled vendor RC-103/4.15 configuration fixture:

| Source | Native L1 / L2 | HA L1 / L2 |
|---|---|---|
| Dose rate | 40 / 120 µrem/h | 0.40 / 1.20 µSv/h |
| Count rate | 200 / 600 counts per 10 seconds | 20 / 60 cps |
| Accumulated dose | 999,999,999 / 999,999,999 µrem | 9,999,999.99 / 9,999,999.99 µSv |

The previous accumulated-dose input maximum was 1,000,000 µSv, below its firmware default. The beta raises it to 10,000,000 µSv, matching the fixture's declared dose alarm maximum. The integration reads device settings and does not write factory defaults during upgrade. Firmware-defined values are not clinical or occupational recommendations.

The sensors continue to report **calculated L1/L2 threshold levels**. The manufacturer's alarm system also includes acknowledgment, silencing and out-of-scale behavior, which these entities do not implement. See [Radiacode alarm behavior](https://radiacode.com/docs/en/100-series/alarms/how-alarms-work/).

## Grouping

Home Assistant's [entity categories](https://developers.home-assistant.io/docs/core/entity/#generic-properties) provide Configuration and Diagnostic classification, not arbitrary device-page subsections. Keep alarm states under Sensors and thresholds under Configuration. The [dashboard example](../examples/alarms-card.yaml) provides Sensors followed by a dedicated Alarms section with all three sources and their controls, using built-in cards only. Existing entity IDs must be selected when installing the example; the integration does not rename them.

## Verification and field acceptance

Local dependency-light regression tests exercise the sparse-record sequence, unchanged and zero register values, rejected/truncated replies, invalid values, expiry/recovery, an in-flight read across reset, unit conversions and preserved identity. Real Home Assistant entity publication and control defaults are covered by the pinned CI runtime matrix. The local Python environment does not contain the supported Home Assistant runtime.

For physical acceptance:

1. Install **2026.11.2b1** through HACS with beta releases enabled and restart Home Assistant. Leave the detector stationary near AIR-1 with the same polling settings.
2. Check the three alarm names and both accumulated-dose thresholds. Existing IDs and device values should remain unchanged; user-assigned names may still override integration defaults.
3. Run at least 30–60 minutes, then overnight. Accumulated Dose and its alarm should remain available while successful observations continue. Check `measurement_source` (`rare_data`, `dose_register`, or briefly `dose_reset`) and `measurement_age_seconds` in entity attributes.
4. Export alarm, Accumulated Dose, Dose Rate, Count Rate and BLE Connected histories together, plus diagnostics. A dose expiry should now have an observable age and optional read error; a primary-rate gap should remain independently visible.
5. Confirm spectrum age and retry state separately. The beta targets sparse-dose availability; physical BLE/proxy reliability and actual read behavior on this detector remain to be tested.
