# React releases

For changes that should ship to npm, run `npm run changeset` from `frontend/`.
Select `@openviking/react`, choose patch, minor, or major, and describe the
change for package consumers. Commit the generated Markdown file with the PR.

After the PR lands on official `main`, **React UI Release** opens or updates
a version PR. Merging that PR publishes the new version to npm after the
release checks pass. Review the version, changelog, and lockfile before merging.

The official repository needs an npm credential via `NPM_TOKEN` and a GitHub
token that can write contents and pull requests. Configure
`REACT_RELEASE_GITHUB_TOKEN` if the default workflow token cannot create PRs
or trigger the required checks. When using the default token, enable the
repository setting allowing GitHub Actions to create pull requests.

For a manual release, push `react@<version>` to the official repository.
The tag must match the React package manifest at that commit.
