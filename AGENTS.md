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
- Write the README for someone installing and using the integration. Keep testing history, compatibility evidence, and implementation details in supporting guides. Include prerequisites only where they help the user complete setup.

## External references and attribution

Maintain the **External references** section at the bottom of [README.md](README.md#external-references) as a bibliography for the resulting build, not a research log.

- With each change, review the affected references alongside the code, fixtures, dependencies, dashboard examples, documentation, or build/release tooling they support. Add newly used sources and update or remove entries whose relevance changed in the same pull request. Review the full list before a release.
- Keep a source only when it supports retained implementation or behavior, a supported configuration, an active example/test, current contributor tooling, or attribution for material still present. A source consulted during research does not automatically qualify. Remove superseded approaches, unused experiments, and obsolete alternatives from the README; preserve dated research reports and Git history.
- Judge currency by relevance, not publication date. An older pinned decoder, firmware declaration, or captured fixture can still support the current build. Do not replace the version actually used with the latest upstream version merely to make a citation look current.
- List only public external sources in this section. Keep project reports, this repository's issues/PRs/commits, local files, private logs, and internal conversations out of the bibliography. Group related resources and avoid duplicate links.
- Credit the author or project, name the resource, and briefly state its current contribution. Distinguish copied/adapted material, protocol or design references, and dependencies/tools; do not imply authorship, adoption, or endorsement that the evidence does not support. Prefer original maintainers' code and official documentation over mirrors or summaries.
- For implementation evidence, link to the exact file at the reviewed commit (and relevant lines when useful), or an identifiable release/edition. Use canonical documentation URLs for general guidance. Honor upstream `CITATION.cff` / `preferred-citation` metadata when provided and include a version-specific DOI or archival identifier when available; do not invent citation metadata.
- Before copying or adapting third-party code, documentation, fixtures, or artwork, check its actual license and attribution requirements. Preserve required copyright, license, SPDX, modification, and NOTICE information in the appropriate files and distributed artifacts. A README link does not replace those obligations. Retained derived material remains relevant even when the original dependency is gone; removing a bibliography entry must not erase required credit.
- Verify new or changed links and confirm that their content supports the stated contribution. Repair moved links to the same work/version; use a verified archival copy where appropriate. A dead link alone is not grounds to remove attribution for retained material. Run the documentation link checker and diff checks, and state additions, removals, or an unchanged-reference review in the PR.

Citation and attribution guidance: [GitHub permanent links](https://docs.github.com/en/repositories/working-with-files/using-files/getting-permanent-links-to-files), [CITATION files](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files), [REUSE copyright and licensing guidance](https://reuse.software/faq/), and the [MIT license terms](https://choosealicense.com/licenses/mit/). Apply the actual upstream license to reused material; do not assume every cited project uses this repository's license.

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
