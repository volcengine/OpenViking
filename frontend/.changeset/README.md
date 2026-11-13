# React releases

For changes that should ship to npm, run `npm run changeset` from `frontend/`.
Select `@openviking/react`, choose patch, minor, or major, and describe the
change for package consumers. Commit the generated Markdown file with the PR.

Changesets remain available for preparing a stable version and changelog with
`npm run version:react`. Merging a PR never publishes a stable version.

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
