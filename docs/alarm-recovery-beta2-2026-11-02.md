# Dose and reconnect recovery beta: 2026.11.2b2

[Documentation index](README.md) · [Current troubleshooting](troubleshooting.md)

## Supplied beta-1 evidence

The private RC-103/firmware 4.15 debug capture covers approximately six hours. Only sanitized findings appear here; the raw log and device identifiers are excluded.

- 4,291 accepted fresh paired readings; one approximately 110-second gap.
- 332 accumulated-dose fallback attempts failed. Each returned a complete eight-byte response body: four echo bytes and four payload bytes. The log does not contain those payload bytes, so their value is unknown.
- Sixteen dose-bearing records, with a maximum observed gap of approximately 28 minutes. A three-minute dose expiry cannot bridge those gaps when the fallback fails.
- 310 of 315 spectrum attempts completed. All five failed transfers subsequently recovered; these failures remain relevant to physical testing.
- During the longest gap, one reconnect validated firmware, then a partial spectrum transfer retired the connection. The next connection received complete buffers but could not decode them until the previous successful identity check's 60-second cooldown elapsed.

Signal and advertisement-source records do not establish which proxy carried the active connection. This capture cannot isolate physical placement as a cause or confirm recorder-state transition durations.

## Protocol boundary

The existing `RD_VIRT_SFR` parser assumed every reply included both status and value. Public [RC-102/4.14 register-read evidence](https://github.com/darkmatter2222/radiacode-firmware/blob/6501c6bdfea8b633c6b3e66e7be28be2326a7e8c/firmware/rc-102/hw1-fw4.14/analysis/vsfr_readout.json) and [firmware analysis](https://github.com/darkmatter2222/radiacode-firmware/blob/6501c6bdfea8b633c6b3e66e7be28be2326a7e8c/firmware.md#25-capability-matrix) distinguish successful status/value pairs from status-only responses. This corroborates strict rejection handling, not a working dose query on RC-103/4.15. No upstream source code or captured device identity is copied into the tests.

Beta 2 keeps the success layout strict, interprets a non-success status before checking for a value, and records payload errors. It does not reinterpret four bytes as a raw dose, switch to an unverified command, or substitute the non-resettable Dose Counter. The register path stops after three consecutive failures until reload or a confirmed firmware/build change. Ordinary reconnects and fresh RareData do not reset this failure budget.

## Resulting behavior

Accumulated Dose and its calculated alarm show the last actual since-reset observation for at most 30 minutes, and only while the live paired-rate lease is valid. The 30-minute bound is a beta policy chosen for the observed approximately 28-minute gaps, not a manufacturer guarantee of dose-report cadence. A fresh rate reading never updates dose age. Failed register reads cannot renew either lease. Initial availability still waits for a real dose record, successful register read, or acknowledged reset.

This changes the beta-1 policy: dose values are no longer treated as three-minute status measurements, and a recent dose observation cannot keep them available through a stale live stream. No dose is estimated between observations. Threshold comparisons can only reflect the last observed total, not unreported accumulation.

Each connection receives its own firmware validation without inheriting a cooldown from prior success. Actual identity failures retain the bounded retry interval. Automatic spectra wait for an accepted pair on the current connection. Existing transport limits, strict schema checks, primary freshness, cached spectra, and spectrum failure limits remain in place.

## Validation and physical acceptance

Synthetic regressions reproduce sparse dose timing and rapid reconnects, exercise malformed/status-only replies, and verify independent expiry and preserved failure budgets. HA runtime coverage publishes the corresponding entity states. Automated checks do not establish detector/proxy acceptance.

For the next stationary-device run:

1. Confirm `2026.11.2b2` is installed, then collect Accumulated Dose, its alarm, Dose Rate, Count Rate, and BLE Connected history together.
2. Confirm dose age increases between actual observations and resets only with new dose evidence. Expect at most three failed fallback attempts before `register_read_paused` becomes true; recurring one-minute failures should stop.
3. Verify dose entities remain available across the previously observed 26–28-minute record gaps while live rates continue. Record any dose gap reaching 30 minutes; do not assume all environments share the observed cadence.
4. After reconnects, check that firmware validation occurs promptly and a new accepted pair precedes automatic spectrum work. Record missing replies, partial transfers, freshness gaps, and recovery separately.
5. Confirm stale live measurements still make dose entities unavailable at the usual live lease. Keep this release beta until an extended run supports its behavior.
