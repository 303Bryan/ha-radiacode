# Release versioning

New releases use calendar versions in the form **YYYY.MM.N**, for example `2026.10.3`. `YYYY` is the UTC publication year, `MM` is the two-digit UTC publication month (`01`–`12`), and `N` is the release line number within that month, starting at 1. Keep the same base version through its prereleases and final release; the next release line increments `N`. Restart at 1 in a new month. If a candidate crosses into a new month before publication, use that month's next available release line.

Use PEP 440 prerelease suffixes: `2026.10.3b1` for a beta or `2026.10.3rc6` for a release candidate. Mark these as GitHub prereleases. A final `2026.10.3` follows its candidates; the next release line is `2026.10.4`. These versions are accepted by Home Assistant and compared correctly by HACS, including months with a leading zero.

## Names used by each system

| Location | Example for the next October candidate |
|---|---|
| Integration `manifest.json` version | `2026.10.3rc6` |
| Changelog heading | `[2026.10.3rc6]` |
| GitHub tag | `v2026.10.3rc6` |
| GitHub display title | `2026.10.3rc6` |
| Optional manual-install ZIP | `radiacode-v2026.10.3rc6.zip` |

HACS identifies installed and available versions by the release tag, not the display title. Future tags and manifests must therefore use the same calendar version. A calendar version is newer than the legacy `2.0.x` versions. Existing published manifests, tags and ZIP contents retain their original versions.

## October 2026 transition

The two published October stable release lines are displayed as `2026.10.1` (tag `v2.0.0`) and `2026.10.2` (tag `v2.0.1`). The current RC5 displays `2026.10.3rc5`, with its original `v2.0.2rc5` tag and integration version. If another candidate is released in October, use `2026.10.3rc6` for its actual version and tag; an October final would be `2026.10.3`.

The naming change does not publish a new build or promote the current beta. Existing installation URLs and HACS version identifiers remain valid.

## Publishing future releases

1. Choose the next calendar version and update the manifest and matching changelog section together. Check for an existing tag before allocating it.
2. Complete the release's required checks on the exact candidate commit. Finish physical acceptance before stable promotion.
3. Run the existing Release workflow on the approved main commit, or publish a checked candidate explicitly with its exact commit. The workflow validates the calendar format, rejects invalid months and zero release numbers, and uses the manifest version for the tag and title.
4. Keep candidates marked as prereleases and excluded from latest stable. Verify the tag target, prerelease flag, manifest version and any uploaded package/checksum after publication.

## Historical display names

Only the GitHub **display titles** were changed on 2026-10-11. Original tags, notes, assets, checksums, targets, publication dates and prerelease flags are preserved. The table uses UTC publication month and numbers distinct legacy release families by their first publication in that month. Original `bN`/`rcN` suffixes and descriptive title text are retained. Changelog entries and links still use the original tags so they continue to identify the exact historical builds.

The historical `v0.5.0` final was published before its betas; its original chronology is preserved. The `v2.0.0` beta was published in July and its final in October, so their display months differ.

| Original tag / installation version | Calendar display version | Published (UTC) |
|---|---|---|
| [`v0.3.0`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.3.0) | `2026.03.1` | 2026-03-06 |
| [`v0.4.0b1`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b1) | `2026.03.2b1` | 2026-03-06 |
| [`v0.4.0b2`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b2) | `2026.03.2b2` | 2026-03-06 |
| [`v0.4.0b3`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b3) | `2026.03.2b3` | 2026-03-06 |
| [`v0.4.0b4`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b4) | `2026.03.2b4` | 2026-03-06 |
| [`v0.4.0b5`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b5) | `2026.03.2b5` | 2026-03-06 |
| [`v0.4.0b6`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0b6) | `2026.03.2b6` | 2026-03-06 |
| [`v0.4.0`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.0) | `2026.03.2` | 2026-03-06 |
| [`v0.4.1b1`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.4.1b1) | `2026.03.3b1` | 2026-03-07 |
| [`v0.5.0`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.5.0) | `2026.03.4` | 2026-03-07 |
| [`v0.5.0b1`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.5.0b1) | `2026.03.4b1` | 2026-03-07 |
| [`v0.5.0b2`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.5.0b2) | `2026.03.4b2` | 2026-03-07 |
| [`v0.5.0b3`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.5.0b3) | `2026.03.4b3` | 2026-03-07 |
| [`v0.5.0b4`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.5.0b4) | `2026.03.4b4` | 2026-03-08 |
| [`v0.6.0`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.6.0) | `2026.03.5` | 2026-03-08 |
| [`v0.6.1`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.6.1) | `2026.03.6` | 2026-03-08 |
| [`v0.6.2`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.6.2) | `2026.03.7` | 2026-03-08 |
| [`v0.6.3`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.6.3) | `2026.03.8` | 2026-03-08 |
| [`v0.6.4`](https://github.com/303Bryan/ha-radiacode/releases/tag/v0.6.4) | `2026.04.1` | 2026-04-26 |
| [`v1.0.0b1`](https://github.com/303Bryan/ha-radiacode/releases/tag/v1.0.0b1) | `2026.06.1b1` | 2026-06-10 |
| [`v1.0.0`](https://github.com/303Bryan/ha-radiacode/releases/tag/v1.0.0) | `2026.06.1` | 2026-06-10 |
| [`v1.1.0b1`](https://github.com/303Bryan/ha-radiacode/releases/tag/v1.1.0b1) | `2026.07.1b1` | 2026-07-05 |
| [`v1.1.0`](https://github.com/303Bryan/ha-radiacode/releases/tag/v1.1.0) | `2026.07.1` | 2026-07-05 |
| [`v1.3.0b1`](https://github.com/303Bryan/ha-radiacode/releases/tag/v1.3.0b1) | `2026.07.2b1` | 2026-07-05 |
| [`v1.3.0b2`](https://github.com/303Bryan/ha-radiacode/releases/tag/v1.3.0b2) | `2026.07.2b2` | 2026-07-05 |
| [`v1.3.0`](https://github.com/303Bryan/ha-radiacode/releases/tag/v1.3.0) | `2026.07.2` | 2026-07-05 |
| [`v2.0.0b1`](https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.0b1) | `2026.07.3b1` | 2026-07-05 |
| [`v2.0.0`](https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.0) | `2026.10.1` | 2026-10-03 |
| [`v2.0.1rc1`](https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.1rc1) | `2026.10.2rc1` | 2026-10-03 |
| [`v2.0.1rc2`](https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.1rc2) | `2026.10.2rc2` | 2026-10-03 |
| [`v2.0.1rc3`](https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.1rc3) | `2026.10.2rc3` | 2026-10-03 |
| [`v2.0.1`](https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.1) | `2026.10.2` | 2026-10-03 |
| [`v2.0.2rc2`](https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.2rc2) | `2026.10.3rc2` | 2026-10-10 |
| [`v2.0.2rc3`](https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.2rc3) | `2026.10.3rc3` | 2026-10-11 |
| [`v2.0.2rc4`](https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.2rc4) | `2026.10.3rc4` | 2026-10-11 |
| [`v2.0.2rc5`](https://github.com/303Bryan/ha-radiacode/releases/tag/v2.0.2rc5) | `2026.10.3rc5` | 2026-10-11 |

## Compatibility references

- [Home Assistant custom integration manifest versions](https://developers.home-assistant.io/docs/creating_integration_manifest/#version)
- [HACS release versions and tags](https://www.hacs.xyz/docs/publish/start/#versions)
- [Python version specification and prereleases](https://packaging.python.org/en/latest/specifications/version-specifiers/)
