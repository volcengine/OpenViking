# React releases

For changes that should ship to npm, run `npm run changeset` from `frontend/`.
Select `@openviking/react`, choose patch, minor, or major, and describe the
change for package consumers. Before committing, edit the generated Markdown
file to include both language sections in the same file:

```md
---
'@openviking/react': patch
---

## en-US

Fix file preview navigation when opening a resource URI.

## zh-CN

修复打开资源 URI 时文件预览的导航行为。
```

Both sections are required and must describe the same change, including any
breaking changes and migration steps. Keep package names and the version bump
in one frontmatter block. Do not create a separate changeset for each language.
Empty changesets created with `npm run changeset -- --empty` do not need notes.

Run `npm run check:release-notes` before committing. CI checks for missing,
duplicate or empty language sections and out-of-date documentation pages.
Reviewers check that the two descriptions match.

Run `npm run version:react` to prepare a stable version. Changesets consumes the
files, updates versions and writes both languages into
`packages/react/CHANGELOG.md` using the configured
[changelog formatter](https://github.com/changesets/changesets/blob/main/docs/modifying-changelog-format.md).
The command then updates the lockfile and generates the Chinese and English
release-note pages under `docs/docs/{zh,en}/guide/changelog.md`. Commit these
release materials together for review before publishing. Merging a PR never
publishes a stable version.

`CHANGELOG.md` is the archive after changeset files are consumed. Keep its
language markers when correcting a released note, then run
`npm run sync:release-notes`. Edit pending notes in their changeset files;
do not edit the generated documentation pages. The documentation shows only
the selected language and current package major, grouped by minor in descending release order. Only minor groups are collapsible, with the newest group expanded by default. All release content within a group is displayed vertically. Older majors remain in the archive. Automatic beta builds are not listed.

On the official repository, pushes to `main` that change
`frontend/packages/react/src/**` or `frontend/packages/icons/**` automatically
publish a beta after release checks pass. This includes merged PRs and direct
pushes. Documentation-only changes do not publish. Each beta reads the last
stable version from npm's `latest` dist-tag and increments its patch, followed
by `-beta.<run>.<attempt>.g<commit>`. For example, stable `0.1.7` produces
`0.1.8-beta.<run>.<attempt>.g<commit>` regardless of the manifest version. Betas are
published with npm's `beta` dist-tag. Install with
`npm install @openviking/react@beta`. Package and lockfile changes made by the
workflow are temporary and are not committed back.
During beta staging, release-note checks and generation use the latest stable
version archived in `CHANGELOG.md`, preserving the prepared stable major even
when npm's `latest` still belongs to an older major.

For a stable release, manually run **React UI Release**, choose `latest`, and
enter an exact stable version such as `0.1.8`. Select the branch or tag to
release. Alternatively, push `react@<version>`; the tag must match the package
manifest at that commit. Stable tags and manual runs are the only ways to
publish `latest`. An already published version is skipped.

When a manual run selects a tag ref, the chosen channel and version inputs still
control the release. Tag-to-manifest validation applies only to tag push events.

Manual runs can also choose `beta`, leaving the version input empty; these use
the same npm stable version rule. The official repository needs `NPM_TOKEN` with
access to `@openviking/react`, or npm trusted publishing configured for
`volcengine/OpenViking` and `.github/workflows/react-release.yml`.
Fork runs skip publishing; workflow changes can be prepared on fork main, while
manual release tags must be pushed to the official repository.
