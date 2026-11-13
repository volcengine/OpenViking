# OpenViking Frontend

The `frontend/` workspace of OpenViking. It holds the React library [`@openviking/react`](./packages/react/README.md), the SVG icon sources it ships, and the Rspress site that documents them. This README is for contributors; to use the library, start from the package README.

Agent-facing development constraints are in [AGENTS.md](./AGENTS.md).

## Layout

- [`packages/react`](./packages/react): the `@openviking/react` source. Business components are in `src/components/`, blocks in `src/modules/`, the request client in `src/client/` and internal shared code in `src/shared/`.
- [`packages/icons`](./packages/icons): SVG sources with their provenance and licenses.
- [`docs`](./docs): the Rspress site. Pages are in `docs/docs/zh/` and `docs/docs/en/`, runnable examples in `docs/docs/_demos/`, and the sidebar in `docs/sidebar.ts`.
- [`.changeset`](./.changeset/README.md): release notes and the npm release process.

For the lists of business components and blocks, see the [business component](./docs/docs/en/components/index.mdx) and [block](./docs/docs/en/modules/index.mdx) overviews, or list the two source directories.

## Development

Use Node.js `^20.19.0 || >=22.12.0` (the `engines` field in `package.json`); CI runs Node.js 22.

```bash
cd frontend
npm ci
npm run dev
```

Open the `/ui/` address that Rspress prints. `npm run dev` builds the library, then runs the Rslib watcher and the Rspress server together. Demos import the library's built package exports, with no source aliases or TypeScript paths, so they update when the watcher rebuilds. The language menu in the navigation switches between Simplified Chinese at `/ui/` and English at `/ui/en/`.

```bash
npm run lint         # Rslint rules and types, then the Prettier check, for the whole workspace
npm run lint:fix     # The same, applying fixes and formatting
npm run typecheck    # tsc --noEmit for the library, the build configuration and the docs
npm test             # Build the library, then run the Node tests, including the public package entries
npm run build        # Build the library and the static docs
npm run preview      # Serve the built docs at /ui/
npm run changeset    # Add a release note for @openviking/react
```

`dev`, `lint`, `lint:fix`, `typecheck`, `test` and `build` run `npm run build:lib` first, so the docs and the tests consume the library's built output. `npm run check` is an alias for `lint`, and `npm run` lists every script. The library is built into `packages/react/dist/` and the docs into `docs/doc_build/`.

Pass files, directories or quoted globs after `--` to limit formatting and lint rules; type checking always covers the whole configured projects:

```bash
npm run lint -- packages/react/src/index.ts
npm run lint:fix -- 'docs/docs/_demos/**/*.tsx'
```

Run commands from `frontend/` and format only the files you changed: the repository root has its own `docs/`, `README.md` and `AGENTS.md`, so a relative path from the wrong directory touches the product documentation. See [Verification and delivery](./AGENTS.md#验证与交付).

## Add a business component or block

1. Create `packages/react/src/components/<name>/` for a business component, or `packages/react/src/modules/<name>/` for a block. The directory name is kebab-case and the entry file has the same name (`<name>.tsx`). `index.ts` is the only export point and `style.css` is the stylesheet entry. Keep the implementation, styles and private subcomponents beside them, and put tests in `packages/react/test/`. Three or more related components that share internal code go in a group directory such as `components/auth/`, and their names carry no group prefix: `components/auth/email-login-form` exports `EmailLoginForm`.
2. Add the entries. In `packages/react/package.json`, add the JS entry and its `style.css` to `exports`. In `packages/react/rslib.config.ts`, add the JS entry and the stylesheet. Import the stylesheet from `components/style.css` or `modules/style.css`, and export the main component and its `<Name>Props` from `src/index.ts`, unless it depends on an optional peer dependency, as `FilePreview` does. Copy the pattern of an existing entry.
3. Document it. Add a page under `docs/docs/zh/components/` (or `modules/`) and the matching page under `docs/docs/en/`, list it on the category overview, and register it once in `docs/sidebar.ts`, which serves both languages. Put runnable examples in `docs/docs/_demos/`, importing from the public package exports. For a component, also add a short `README.md` and `README_CN.md` in its directory: a one-line description, the public entry and a link to the docs. The [documentation conventions](./AGENTS.md#文档写法) apply to all of these.
4. When the change should ship to npm, run `npm run changeset`, select `@openviking/react`, choose the bump and add both `en-US` and `zh-CN` notes to the generated file. Run `npm run check:release-notes` and commit the file. See [React releases](./.changeset/README.md) for the format and release process.
5. Run `npm run lint`, `npm run typecheck`, `npm test` and `npm run build`, then open the example in a browser (`npm run dev`) and try it.

## Continuous integration and deployment

- The [UI workflow](../.github/workflows/ui.yml) lints, type-checks, tests and builds the workspace for pull requests and pushes that touch `frontend/**`, through the shared [build action](../.github/actions/build-ui/action.yml).
- The product documentation workflows ([GitHub Pages](../.github/workflows/docs.yml) and [TOS](../.github/workflows/docs-tos.yml)) also watch `frontend/**`. They build both sites and copy this site's output into the product documentation's `ui/` directory before publishing. On GitHub Pages, `/ui/` is part of the existing complete site artifact; on TOS, it is uploaded to the existing documentation bucket with the existing repository secrets.
- A branch push does not publish the production website; production publication follows the `main` workflows. The domain, the Pages environment rules and the TOS secrets are repository administration settings.
- The [release workflow](../.github/workflows/react-release.yml) publishes source changes on `main` as npm betas. A stable release needs a manual workflow run or a matching `react@<version>` tag. Local builds and tests never publish.

Do not deploy this site as a second independent GitHub Pages site in this repository: a new artifact would replace the product documentation. The deployment rules are in [Static deployment](./AGENTS.md#静态部署).

## License

The workspace inherits the repository's AGPL-3.0-only license. See [LICENSE](../LICENSE).
