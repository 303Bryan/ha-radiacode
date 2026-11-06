# Documentation

Start with the [installation guide](../README.md).

## Current guides

| Guide | Contents |
| --- | --- |
| [Using Radiacode](usage.md) | Entities, controls, configuration, spectrum action, and dashboard examples |
| [Troubleshooting](troubleshooting.md) | Bluetooth proxies, unavailable readings, spectrum recovery, logs, and diagnostics |
| [Architecture](architecture.md) | Integration lifecycle, transport, decoding, freshness, and storage |
| [Contributing](../CONTRIBUTING.md) | Development environments, tests, documentation, and pull requests |
| [Agent instructions](../AGENTS.md) | Repository rules for coding assistants |
| [Release versioning](release-versioning.md) | Calendar versions, release checks, and historical tag mapping |
| [Changelog](../CHANGELOG.md) | Changes by published version |

## Evidence and historical investigations

These reports describe specific builds and captures. A statement that acceptance was pending or a defect remained unresolved applies to that report's date. Use the current guides and the relevant stable-release assessment for present behavior. Preserve the original reports and their paths so published release links remain valid.

| Date | Report |
| --- | --- |
| 2026-11-06 | [Sparse dose and automatic spectrum recovery: 2026.11.2b3](dose-spectrum-beta3-2026-11-06.md) |
| 2026-11-02 | [Dose and reconnect recovery: 2026.11.2b2](alarm-recovery-beta2-2026-11-02.md) |
| 2026-11-02 | [Alarm availability beta: 2026.11.2b1](alarm-availability-beta-2026-11-02.md) |
| 2026-11-02 | [Stable field assessment: 2026.11.1](stable-field-review-2026-11-02.md) |
| 2026-11-01 | [Firmware 4.15 evidence and beta acceptance](firmware-415-beta-2026-11-01.md) |
| 2026-10-31 | [Beta 4 stability comparison](beta4-stability-review-2026-10-31.md) |
| 2026-10-30 | [1XX quality beta](1xx-quality-beta-2026-10-30.md), [beta 2 connection review](beta2-connection-review-2026-10-30.md), [beta 3 connection review](beta3-connection-review-2026-10-30.md) |
| 2026-10-11 | [Stable field assessment: 2026.10.3](stable-field-review-2026-10-11.md) |
| 2026-10-10 | [RC2 field review](rc2-field-review-2026-10-10.md) |
| 2026-10-09 | [Connectivity review](connectivity-review-2026-10-09.md) |

Additional references: [protocol and stability research](protocol-and-stability-research.md), [RC4 protocol evidence](protocol-connectivity-rc4.md), and [firmware-fixture provenance](../tests/fixtures/firmware/provenance.json).

The [original project specification](archive/initial-project-spec.md) is retained only as historical context. Its connection strategy, requirements, and examples are superseded by the current implementation and architecture guide.
