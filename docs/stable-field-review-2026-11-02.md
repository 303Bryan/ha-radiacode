# Stable field assessment: 2026.11.1

The RC-103 / firmware 4.15 overnight capture supports stable promotion of `2026.11.1b1`. The final release changes diagnostic labels and release metadata; acquisition, decoding, numerical conversion, command deadlines, reconnect behavior and freshness decisions remain those of the tested beta.

## Observed field result

Home Assistant 2026.10.4, an RC-103 running application build `Oct 15 2026 10:18:10`, and a detector stationary near an Air-1 Bluetooth proxy were observed from **November 1, 2026, 20:38:10 to November 2, 08:20:07 MST**. This is an 11-hour, 42-minute window. The supplied diagnostics identify integration version `2026.11.1b1`.

| Measure | Result |
| --- | ---: |
| Ordinary measurement transfers | 8,420 successful; zero failed |
| Accepted fresh readings | 8,416 |
| Longest accepted-reading gap | 15.05 seconds |
| Gaps exceeding the 60-second freshness limit | Zero |
| Complete ordinary buffer decodes | 8,419 / 8,420 |
| Configuration downloads | Zero |
| Spectrum downloads | 630 successful / 634 attempts |
| Successful reconnect/initialization sequences visible | Four |

The one held buffer occurred immediately after reconnect, before firmware reconfirmation; the next accepted reading followed promptly. It was the intended profile guard, not the former byte-22 alignment error. The other three reconnects initially produced no accepted live sample. Cached samples did not renew freshness.

The expected bundled profile was active. Battery/status, accumulated-dose and independent-counter records decoded successfully. This run observed 26 rare/status records and 27 counter records. It did not observe group-5 measurement-session or alarm-event records, whose validation remains fixture-based.

Each incomplete spectrum response was rejected within about 2.1–2.6 seconds of transfer start. The old complete snapshot was retained, the connection was retired, and the next scheduled spectrum read succeeded after the normal five-minute delay. The four resulting gaps between accepted radiation readings were 10.962, 15.050, 11.021 and 11.014 seconds. None expired measurement freshness.

All four connection requests selected the nearby proxy as their requested source; it was the only listed path in three cases. The backend did not expose a definitive active-route identifier. No API disconnect for that proxy appeared in the window. Other proxies continued to report disruptions. Both detector placement and software differed from the earlier failed overnight run, so the improvement cannot be assigned entirely to either change.

## Final corrections and validation

Downloadable diagnostics now preserve only known configuration-source enum values and bundled-profile labels at their specific protocol paths. Device names, serials, Bluetooth addresses, proxy sources and arbitrary names remain redacted. Home Assistant runtime tests check both useful-label preservation and identity redaction without mutating coordinator state.

Debug messages now use the declared dose unit for real-time, raw and database records: rem/h for the supported 4.15 declarations, with the existing R/h label retained for legacy fallback. An offline comparison against the published beta checked all five extracted 1XX 4.15 layouts and the legacy fallback: decoded records, sensor values and decoder status were identical.

The pinned Home Assistant matrix covers 2026.9.4, 2026.10.0, 2026.10.1 and 2026.10.4. It exercises actual integration setup/unload functions and fresh coordinator creation with controlled identity replies and platform forwarding. Profile activation after reload requires no configuration download or persisted profile. This is automated lifecycle coverage, not an additional physical reload/soak measurement. The physical log begins after startup.

## Boundaries retained for stable

- Full-frame validation, primary-poll priority, bounded recovery, optional-transfer retry budgets and receipt-based freshness remain in force.
- Device-relative record timestamps ran roughly 56–80 seconds ahead of receipt in this capture. Analysis used HA receipt time and accepted-sample progression. The existing timestamp anchor is unchanged; these timestamps are not independently verified wall-clock measurements.
- The known-build profiles identify supported vendor model/version/build combinations, not cryptographic attestation of hardware flash contents.
- The field result covers this RC-103/4.15/proxy setup. Other 1XX models and unobserved record types retain their documented automated coverage. ZERO remains a separate update.
- Isolated incomplete spectrum transfers remain possible. Users should inspect `acquired_at` and automatic retry/pause state instead of interpreting a retained histogram as a new successful read.

## Evidence provenance

Raw logs, diagnostics, addresses and serial numbers are not published. The locally reviewed inputs have these SHA-256 digests:

- Overnight log: `59102d6901c4c387820d2be03a64cd0388feb0bdabe03bdbddc5b08a198d7bc5`
- Diagnostic snapshot: `2643d3e9f73d3c3fa05c26471d97b64c0f80db4952e039710c048f4c9eb5725f`

The accepted beta is commit `c20f7d415ec8e2a13c5fa3e9c462596cd188de37`, tag `v2026.11.1b1`. See [firmware-layout provenance](firmware-415-beta-2026-11-01.md) for vendor artifacts, build matching and automated fixture distinctions. The stable release notes identify the final tested commit and downloadable package checksum.
