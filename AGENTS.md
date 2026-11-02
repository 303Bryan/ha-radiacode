# Repository instructions

## Start here

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md) before implementation changes. Use [docs/README.md](docs/README.md) to find current guides and dated evidence.

Check branch, base commit, and working-tree status. Preserve unrelated edits. Do not treat the archived initial specification or a dated beta report as current implementation instructions.

## Scope and compatibility

- Keep runtime files inside `custom_components/radiacode/`.
- Use Home Assistant's Bluetooth framework, Bleak, and the retry connector. Preserve ESPHome proxy support and the persistent BLE connection between polls.
- Preserve the domain, existing entity unique IDs, configuration entries, and established units unless the task explicitly requires a migration.
- Follow the HA minimum in `hacs.json` and the runtime matrix in `.github/workflows/validate.yml`.
- Keep documentation cleanup separate from runtime behavior changes.

## Behavior that must remain covered

- Serialize BLE commands; reject incomplete frames and retire failed streams.
- Keep connection/command deadlines, cancellation, and optional-operation retry budgets bounded.
- Give primary measurements priority over maintenance.
- Publish dose/count from the same accepted sample. Cached values, repeated timestamps, empty buffers, and suppressed outliers cannot extend freshness.
- Keep independent expiry timers and distinguish radiation availability from link/control availability.
- Retain only complete spectrum snapshots and expose acquisition age and pause state.
- Validate declared record layouts and units; do not guess widths from firmware version alone.
- Invalidate device-specific configuration on identity/build changes and keep private storage failures separate from polling.
- Redact identities in diagnostics. Do not commit private logs, serial numbers, addresses, or unreviewed device configuration.

## Evidence, checks, and handoff

Use the commands in CONTRIBUTING.md. Add focused regressions for behavior changes; documentation-only changes need link and diff checks. Run the release-gate tests when changing publishing logic.

Separate local test results, CI results, and physical observations. A skipped HA runtime suite is not a pass. Label synthetic fixtures, captured data, and untested model combinations accurately.

Update current guides and Unreleased notes for relevant changes. Preserve historical report URLs and published release identities. Follow the release procedure for any requested publication; do not use documentation work as a reason to publish a new release.
