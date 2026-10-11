# RC2 field review — October 10, 2026

**Finding:** RC2 separates connected controls from stale radiation successfully, but measurement selection can still block valid live updates. Raw/database timestamps are ahead of the live stream; choosing the largest timestamp across record types and then enforcing one shared watermark rejects advancing `RealTimeData` samples. Incomplete BLE responses also remain a separate, genuine transport problem.

## Evidence and scope

The supplied RC2 config-entry diagnostics identify integration **2.0.2rc2**, Home Assistant **2026.10.1**, Python **3.14.6**, detector firmware **4.14**, empty integration options, and a 60-second measurement lease. The published RC2 source is commit `f39cdcbb397415826f208d5d06cc7394f95066df`. The reviewed local commit `29d8fb8` has an identical source tree; the local commit identifier does not establish what was installed on the detector host.

The user reports the connected AIR-1 is running **2026.9.1**. Its project firmware version and actual TCP-buffer configuration have not been supplied.

Evidence consists of the supplied diagnostics, `activity (1).csv`, the earlier `activity.csv`, and `home-assistant_radiacode_2026-10-11T02-20-38.521Z.log`. Raw inputs and device/source identifiers are excluded from this repository. CSV timestamps are UTC; the HA debug log uses America/Denver local time. All times below are **MDT (UTC−06:00)**.

The new CSV contains **45,995 rows** spanning October 9 00:00:17.690 through October 10 20:19:25.063, about 44 hours 19 minutes. It includes 15 controls, Radiation Alarm, and BLE Connected; it does **not** contain dose/count sensor histories. Most of this export predates RC2. Radiation Alarm briefly becomes `unknown` at October 10 **13:50:37.310**, followed by markedly different control availability; this is an **estimated reload boundary**, not independent proof of the installation time.

The debug log spans October 10 **15:42:17.661–20:20:38.527**. The conservative comparison window ends at the CSV cutoff, **20:19:25.063**.

## Availability observed in the CSV

| Window / entity | Unavailable or BLE-off episodes | Observed duration | Share of window | Median / maximum |
|---|---:|---:|---:|---:|
| Earlier export, controls/alarm; 44h13m | 1,659 | 47,696.630 s | 29.959% | 24.952 / 117.453 s |
| Earlier export, BLE Connected | 171 | 1,210.807 s | 0.761% | 5.216 / 35.139 s |
| Estimated RC2 window, 13:50:37–20:19:25; controls | 23 | 299.938 s | 1.286% | 6.714 / 46.793 s |
| Estimated RC2 window; BLE Connected | 23 | 299.941 s | 1.286% | 6.713 / 46.794 s |
| Estimated RC2 window; Radiation Alarm | 49 | 2,240.773 s | 9.606% | 54.807 / 75.353 s |
| Log-covered comparison, 15:42:17–20:19:25; controls | 15 | 178.486 s | 1.073% | 7.267 / 35.930 s |
| Log-covered comparison; BLE Connected | 15 | 178.487 s | 1.073% | 7.267 / 35.931 s |
| Log-covered comparison; Radiation Alarm | 34 | 1,612.681 s | 9.699% | 60.490 / 75.353 s |

Control figures use Display Brightness as the representative entity; all 15 controls transition together, with millisecond differences. In the estimated RC2 window every control outage overlaps reported BLE-off. **39 of 49** alarm outages have no such overlap; only **121.398 seconds** of alarm unavailability overlap BLE-off. In the precise log-covered comparison, **28 of 34** alarm outages have no overlap, and the overlap is **49.384 seconds**.

These are **published BLE states**, not a complete physical-link trace. A failed transfer and successful reconnect can finish inside one primary poll before HA publishes another binary-sensor state. The debug log records **90 successful connection establishments**, far more than the 15 exported BLE-off intervals during the overlapping observation window. Therefore BLE-on in the CSV alone cannot prove uninterrupted transport. The successful same-generation command sequences provide the stronger evidence for the timestamp failure.

The earlier export includes one right-censored final outage and a short initial interval without a recorded BLE state. The two exports overlap in time and have different software/windows; their whole-file totals are not a controlled before/after performance test.

## The timestamp failure is visible directly

The log contains **2,827 decoded sensor polls**:

- Selected `RealTimeData`: **2,594**; selected `DoseRateDB`: **155**; selected `RawData`: **45**; no selected measurement: **33**.
- Every selected database timestamp is **40.779–47.936 seconds ahead** of receipt. Every selected raw timestamp is **124.842–127.767 seconds ahead**.
- All **155** database selections and **25** raw selections occurred in buffers that also contained live `RealTimeData`. RC2 selected the raw/database record because its timestamp was greater.
- Across the 34 CSV alarm outages in the log-covered window, every onset occurs **60.000–60.004 seconds** after the last accepted sample. The last accepted type is raw in **31**, database in **2**, and live in **1**.

A concrete first sequence, with source log line numbers:

1. **15:42:17.662**, lines 2–7: a complete buffer includes live dose rate **0.1268 µSv/h**, but selects raw dose rate **0.9238 µSv/h** with timestamp **15:44:24.809990**, about 127 seconds ahead of receipt.
2. **15:42:22.655**, lines 10–16: another complete buffer provides `RealTimeData` timestamp **15:42:22.059990** with normal receipt lag **0.596 seconds**. The coordinator reports `fresh=False` and retains the future raw reading.
3. **15:43:34.657**, line 208: another raw reading is accepted with timestamp **15:45:41.809990**.
4. **15:44:34.658**, CSV: the 60-second lease expires and Radiation Alarm becomes unavailable. Log line 431 shows a successful, connected poll rejected as stale.
5. **15:45:40.817**, CSV, and log line 551: a live timestamp finally passes the shared watermark and availability returns, after **66.159 seconds** unavailable. The elapsed time since the previous accepted sample is **126.160 seconds**.

This also explains why cached raw values can affect calculated alarms despite healthy live background readings. The differing record timestamps are observed facts; their firmware-level meaning remains unverified.

The primary reference [cdump/radiacode webserver example](https://github.com/cdump/radiacode/blob/master/src/radiacode/examples/webserver.py) selects only `RealTimeData` for current rate charts and sorts timestamps within that stream. Its [DATA_BUF decoder](https://github.com/cdump/radiacode/blob/master/src/radiacode/decoders/databuf.py) maps every record offset through the same base-time formula, but does not document that all record types share a comparable live clock. These references support live-stream preference; the captured field trace is the reason for changing selection and watermark behavior, without guessing firmware timestamp semantics.

A replay check over the recorded selected-measurement summaries, partitioned by **connection generation and record type**, finds all **2,594 live timestamps advancing**, with zero live replays. RC2 actually accepts only **1,231** live samples. Thus **1,363 advancing selected live samples** are blocked by the shared watermark in this trace. Preferring live data within mixed buffers would additionally change the 180 raw/database selections described above. This is a bounded analysis of logged summaries, not a full wire replay or a prediction of hardware performance; timestamps of live records overridden inside mixed buffers are absent from those summaries.

## Transport loss and decoder limitations remain

The log contains **4,034 commands**: DATA_BUF has **2,904 successes, 84 incomplete responses, and 1 no-response failure**; register batches have **770 successes, 2 incomplete responses, and 3 no-response failures**. There are no spectrum commands in this log. Diagnostics show automatic spectrum acquisition paused after three failures, with its last attempt over six hours old. These DATA_BUF failures therefore persist independently of active automatic spectrum acquisition.

For example, at **16:05:19.055**, log line 2871, a DATA_BUF reply declares **390,103,040 bytes** but receives only 16. That is implausible framing, not a legitimate large measurement buffer. Other failures receive fewer bytes than declared. The integration must continue rejecting such replies and retiring the unsafe stream. The HA log alone cannot localize loss to RF, peripheral, proxy forwarding, or the host/API path.

Of 2,827 decoder calls, **2,822 complete**; **5 stop on an incomplete `UserData` record** (`gid=4`, expected 16 payload bytes but only 6 remain). This warrants captured-buffer investigation. It does not justify guessing a new record length or accepting malformed measurement data.

## Proposed correction and hardware acceptance

The follow-up candidate should:

1. Prefer valid paired `RealTimeData` within a buffer before comparing fallback timestamps.
2. Track advancing timestamps separately by record type and connection generation, so fallback timestamps cannot block live measurements. Replayed, empty, invalid, or suppressed samples must still leave the lease unchanged.
3. Expose decoder completion, candidate counts/timestamps, selected source, and rejection reasons without publishing raw device data or identifiers.
4. Keep strict complete-frame validation, bounded retries/teardown, independent measurement expiry, and the RC2 control availability fix.

Validate regression cases for mixed sources, future fallback timestamps followed by live records, per-source replay/regression, and reconnect resets. Then run the corrected candidate on the **same detector and proxy with controlled, unchanged settings**. Compare advancing live updates, alarm availability, command failure rate, reconnects, poll latency, and complete spectrum reads over a prolonged run. Hardware acceptance remains outstanding; software tests and summary replay cannot establish proxy throughput or detector stability.
