# Contributing

Start with the [architecture](docs/architecture.md) and [documentation index](docs/README.md). Report bugs and propose changes through [GitHub issues](https://github.com/303Bryan/ha-radiacode/issues/new/choose).

## Working copy

Work on a branch based on current `main`. Check `git status` first and preserve unrelated local changes. Keep runtime changes, documentation reorganizations, and releases reviewable as separate concerns.

All installed files belong under `custom_components/radiacode/`. Tests, examples, and research reports are development material and must not be required at runtime.

## Lightweight regression suite

The main test job uses **Python 3.12** with pytest; a Home Assistant installation is not required.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install pytest
python -m pytest tests/ --ignore=tests/ha_runtime -q
```

When changing transport, decoding, freshness, or state behavior, add focused tests for the failure being addressed. Preserve existing fixtures and distinguish synthetic input from actual captures.

## Home Assistant runtime tests

Use a separate **Python 3.14** environment for each pinned runtime. For example:

```sh
python3.14 -m venv .venv-ha
. .venv-ha/bin/activate
python -m pip install -r tests/ha_runtime/requirements-ha-runtime.txt
python -m pytest tests/ha_runtime/ -q
```

That file pins the minimum supported Home Assistant version. The other requirements files and CI cover additional releases; see [validate.yml](.github/workflows/validate.yml) for the current matrix. Use a clean environment when switching dependency sets.

These tests control BLE responses. A skipped runtime module means the environment is unavailable, not that runtime compatibility passed. Report local and CI results separately.

## Repository checks

```sh
python scripts/check_docs.py
node --test tests/release-validation.test.cjs
git diff --check
```

The documentation checker verifies repository-relative Markdown links, including fragments, and local links in the new user/developer guides. HTTP links are not fetched. The Node tests exercise the release validation gate without publishing anything.

CI also runs HACS validation and hassfest without ignored checks. Both can be run through the **Validate** workflow's manual trigger.

## Documentation

- Keep the root README focused on features, installation, and everyday use. Put test evidence, firmware details, and implementation caveats in supporting guides; avoid repeating what the setup instructions already make clear.
- Put current user instructions in `docs/usage.md` and `docs/troubleshooting.md`.
- Explain implementation decisions in `docs/architecture.md`.
- Index new guides and dated field reports in `docs/README.md`.
- Preserve published report paths. Label superseded evidence rather than rewriting historical findings.
- Update English strings and translations together when changing UI text.
- Keep supported-device claims tied to physical evidence or explicitly labeled automated coverage.
- Update the relevant guide and the Unreleased changelog when behavior or maintenance procedures change.

### External references and credit

Follow the [source-maintenance and attribution policy](AGENTS.md#external-references-and-attribution). Review affected README references in each PR and the complete list before a release. Keep only external sources relevant to the resulting build, explain each source's contribution, and remove obsolete entries. Preserve applicable attribution and license notices for retained third-party material. An older source can remain relevant; a recent publication does not make an unused source current. Verify new or changed external links separately from the local-link checker.

## Bug reports and field evidence

Include the integration, HA, detector model, firmware, proxy project, and ESPHome build versions. Describe the expected behavior, actual behavior, steps, and relevant timestamps.

Use downloadable diagnostics and a short debug-log window. Review logs and captures before posting: raw logs can contain Bluetooth addresses, names, serials, and proxy identifiers. Keep private captures out of commits. See [troubleshooting](docs/troubleshooting.md) for collection steps.

Protocol or transport changes need a relevant physical acceptance run before stable promotion. Record detector/build/proxy details, measurement gaps, failed transfers, reconnects, and spectrum results. Do not present a passing mocked test or another model's run as hardware acceptance.

## Pull requests and releases

Explain the problem, resulting behavior, validation, and remaining limits. Document skipped checks.

Release versions, tags, changelog headings, and prerelease flags must agree. Do not rewrite published tags or historical artifacts. Follow [release versioning](docs/release-versioning.md); the Release workflow requires a successful Validate run on the exact selected `main` commit before it can publish.
