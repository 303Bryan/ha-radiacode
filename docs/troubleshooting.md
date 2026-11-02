# Troubleshooting and Bluetooth proxies

[Documentation index](README.md) · [Installation](../README.md)

## ESPHome Bluetooth Proxy Setup

For the best results with BT proxies:

1. Flash an ESP32 board with the [ESPHome Bluetooth proxy firmware](https://esphome.github.io/bluetooth-proxies/)
2. Place the proxy within ~5 m of your Radiacode device
3. Ensure the proxy is added to HA (it will appear as an ESPHome device) and active Bluetooth connections are enabled

**Tips for reliable operation:**
- Keep the proxy within strong signal range of the Radiacode (RSSI better than −80 dBm)
- Check the proxy's configured active connection capacity and available slots. The detector holds one slot while connected; capacity depends on the proxy hardware and firmware.
- If the sensor becomes unavailable, check measurement freshness and transfer diagnostics as well as Bluetooth signal and proxy capacity


## Known Limitations

- **Large Bluetooth transfers** — weak links or busy proxies can stall spectrum/configuration transfers. The integration rejects incomplete responses, reconnects for subsequent polls, and backs off failed automatic spectrum reads. A stale data-buffer drain failure aborts initialization instead of continuing on a damaged stream; records sent during an interrupted read may be lost.
- **Measurement freshness** — dose and count remain paired from the same sample. Empty buffers, repeated device timestamps and suppressed outliers do not renew freshness; after three poll intervals (at least 60 seconds) without a new accepted sample, radiation entities become unavailable, including during a reconnect. Diagnostics distinguish measurement age from a successful Bluetooth request.
- **Outlier suppression delay** — a dose/count rate reading more than 50× above the current baseline is held for confirmation by a subsequent measurement. Suppressed values are logged as warnings; interrupted transfers and stale readings can extend the interval before confirmation.
- **Signal Strength while connected** — a connected BLE peripheral stops advertising, so no fresh RSSI is available during an active connection; the sensor holds the last observed value until the next advertisement.
- **RareData update rate** — Battery, Temperature, and Accumulated Dose are updated by the device approximately once per minute, regardless of the poll interval.
- **Single connection** — The Radiacode can only maintain one BLE connection at a time. While this integration is connected, the Radiacode mobile app will not be able to connect to the device (and vice versa).


## Troubleshooting

### Sensor goes unavailable periodically

Unavailable readings can result from dropped connections, incomplete transfers, or a device that has stopped delivering fresh measurements. Recent radiation readings remain available during a brief reconnect for up to 60 seconds (or three configured polling intervals, if longer). Diagnostics mark retained readings as cached; an independent timer expires them if fresh samples do not resume. Turning the BLE Connection switch off makes measurements unavailable immediately.

Check diagnostics for measurement age and command failures, then check:
- **RSSI** — look in HA logs for `RSSI=` values on the proxy. Below −85 dBm is marginal; below −95 dBm is unreliable. Move the proxy closer.
- **Proxy slot usage** — the log will show `slots=X/3 free`. If no slots are free, other devices may be competing for the proxy's connection capacity; the configured limit can differ.
- **Device battery** — a low battery can cause the Radiacode to disconnect unexpectedly.

### Integration fails to set up / "Cannot connect"

- Confirm the Radiacode is powered on and not connected to another device (phone app, etc.)
- Verify the MAC address is correct
- Check HA logs (`Settings → System → Logs`) for detailed error messages
- Try moving a Bluetooth proxy closer to the device

### Spectrum stays unchanged through a Bluetooth proxy

An incomplete spectrum read keeps the last complete snapshot; check diagnostics for snapshot age and declared/received bytes. The integration releases the affected connection and backs off automatic retries. After three consecutive failures, it pauses automatic spectrum acquisition so repeated large transfers stop interrupting radiation monitoring. The Spectrum entity shows `acquired_at` and `automatic_paused`; diagnostics also show `consecutive_failures` and `last_error`. `retry_in_seconds` is empty while paused. After addressing the connection problem, a successful manual **current-spectrum** action resumes automatic acquisition. Resetting the current spectrum or reloading the integration also starts a new bounded series of attempts. An accumulated-spectrum read does not resume current-spectrum acquisition.

Spectra, configuration and the register directory have a 30-second transfer deadline once response packets start arriving; ordinary commands and bulk reads without a first reply have ten seconds. A two-second packet stall still fails the transfer. If notification bytes were dropped, a longer timeout cannot recover them.

Correlate the HA command timestamp with the connected proxy's log. `Failed to send notify data response` means the proxy failed to forward a notification to HA. [ESPHome's warning implementation](https://github.com/esphome/esphome/blob/2026.9.0/esphome/components/bluetooth_connection/bluetooth_connection_hub.cpp#L360-L385) logs the first forwarding failure per connection, so one warning can accompany several missing notifications. The warning alone does not establish the underlying TCP-buffer cause.

For an ESP32 proxy already built with ESPHome **2026.9.0 or later**, a controlled test can set the following in its ESPHome configuration (merge into an existing `network:` block):

```yaml
network:
  tcp_send_buffer: 16kB
```

[ESPHome documents this setting](https://esphome.io/components/network/#configuration-variables) for bursty senders, and the [2026.9.0 changelog](https://esphome.io/changelog/2026.9.0/) records its introduction. Its effectiveness for Radiacode remains unverified. Larger buffers use more memory while data is queued. Compare repeated complete 1,024-channel reads and forwarding warnings before/after on the same connected proxy, keeping other settings unchanged.

### Enabling debug logging

Add the following to your `configuration.yaml` and restart HA (or use **Settings → Devices & Services → Radiacode → Enable debug logging** for a temporary session):

```yaml
logger:
  logs:
    custom_components.radiacode: debug
```

Debug logs include per-poll timings, BLE notification reassembly details, and decoded record distributions. Capture the shortest useful failure window, then disable temporary logging. Review logs for Bluetooth addresses, serial numbers, device/proxy names, and other identifying information before posting them. Downloadable diagnostics redact identifiers; raw debug logs can still contain them.

The command summaries identify the virtual string/register target, response sizes, notification count and size distribution, first-byte latency, maximum gap, lock wait, connection generation and failure category. Radiation logs identify the selected sample type/time, opaque flags and device-versus-receipt progression. Optional-operation errors remain separate from the primary polling result. Histories are bounded; routine logs do not print full spectra or configuration contents.

On the proxy, look for notification-forwarding failures, dropped BLE events, TCP congestion and poor reception. An advertisement's source/RSSI can differ from the connected proxy and can be stale while connected. Record both the proxy project version and ESPHome build version; they are separate versions. [ESPHome's proxy documentation](https://esphome.io/components/bluetooth_proxy/) explains reception and shared-radio constraints.

### Downloading diagnostics

From the device page, click the three-dot menu → **Download diagnostics** to get a JSON dump of connection statistics, the latest sensor/settings snapshot, device-health readings, and the device's self-describing **SFR register directory** — a listing of every register the firmware supports with its address, size, type, and signedness (Bluetooth address and device name are redacted).

The download also includes bounded transport history, maintenance status, measurement freshness and spectrum attempt/success ages. Device and proxy identifiers are redacted. Full research, primary references and hardware acceptance criteria are in [protocol and stability research](protocol-and-stability-research.md).

`runtime.data_buf` also includes decoded record types, encountered record groups (`record_group_counts`, keyed by `eid:gid`, including known skipped groups), decoder stopping reason and offset, valid/invalid measurement candidates and their timestamp ranges, and the last 20 sample decisions with UTC receipt times. `runtime.freshness.last_sample_rejection` distinguishes absent pairs/timestamps, repeated or regressed timestamps, and outlier suppression. A successful Bluetooth command can still produce no fresh measurement; these fields explain that distinction without a raw packet capture.

Live sensors prefer the newest valid `RealTimeData` pair even when raw/database records have later timestamps. Raw/database pairs remain fallbacks when no valid live pair is present. Timestamp replay/regression checks are separate for each record type in the current connection, so a fallback cannot block the next live reading and switching types cannot renew a replayed sample. See the [October 10 RC2 field review](rc2-field-review-2026-10-10.md).

The integration reads and caches the detector's configuration to distinguish the modern six-byte dose counter from older group-4 records. It publishes a separate **Dose Counter** sensor; the existing resettable **Accumulated Dose** sensor retains its meaning. Counter-only updates cannot renew live radiation freshness.

Response headers can span notifications, and declared response sizes must fit the detector's negotiated transmit limit. **Confirm Bluetooth writes (experimental)** is disabled by default. When enabled, it uses acknowledged writes only if the characteristic supports them; a stalled write retires the transport without changing modes or replaying the command. Leave proxy settings constant when comparing this option, and turn it off if connection establishment or polling stalls. Reload after changing detector firmware so configuration is read again.

The manufacturer's manuals describe log entries being deleted after reading and connected measurements being forwarded without being stored in autonomous history. Reconnecting restores acquisition but cannot promise to recover every missed measurement. See [RC4 protocol and connectivity evidence](protocol-connectivity-rc4.md) for the source boundaries and device acceptance checks.

Device controls use the active Bluetooth link independently of radiation freshness. A connected device's known settings and reset buttons remain usable while radiation is stale. Unknown settings, an inactive link, user-disabled BLE and shutdown keep the corresponding controls unavailable. Radiation still expires on its own bounded freshness window.

Connection establishment has a 30-second total deadline around connector retries, followed by separately bounded cleanup when necessary. Device initialization commands and the coordinator's single retry have their own deadlines; 30 seconds is not a bound on the complete poll/recovery cycle. See the [October 9 connectivity review](connectivity-review-2026-10-09.md) for the supplied evidence and next hardware test.

## Accumulated Dose Alarm becomes unavailable every three minutes

In versions before 2026.11.2b1, this alarm could expire while live dose/count readings continued because the periodic dose records were sparse. The beta adds a bounded individual dose-register read and gives the numeric Accumulated Dose sensor the same freshness rule as its alarm. Both still become unavailable after 180 seconds without a successful observation.

Inspect `measurement_source`, `measurement_age_seconds`, and `fresh` on the accumulated-dose entities. Downloaded diagnostics include the same information under `runtime.accumulated_dose`, plus optional read errors under `runtime.maintenance.errors`. Export histories for Accumulated Dose, its alarm, Dose Rate, Count Rate, and BLE Connected together to distinguish a missing dose observation from a wider stream outage. See the [beta field review](alarm-availability-beta-2026-11-02.md).
