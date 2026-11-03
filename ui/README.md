# OpenViking UI

An isolated npm workspace for reusable React modules and an Rspress component documentation site. The initial scaffold includes one example, `StatusBadge`; it does not migrate any existing business components.

Agent-facing development constraints are documented in [AGENTS.md](./AGENTS.md).

## Development

Use Node.js 22.12+ (CI uses Node.js 22).

```bash
cd ui
npm ci
npm run dev
```

Open the `/ui/` URL printed by Rspress. The official workspace development plugin starts the library's Rslib watcher before the documentation server, so no manual library build is required. Demos consume the built package exports and update when the watcher rebuilds them.

The navigation language menu switches between English at `/ui/` and Simplified Chinese at `/ui/zh/`. Existing English URLs remain unchanged.

```bash
npm run check          # Build the library, then type-check all workspaces
npm run format:check   # Check workspace formatting
npm run build          # Build the ESM library, declarations, CSS, and static docs
npm run preview        # Serve the built documentation at /ui/
```

## Layout

- `packages/react`: private `@openviking/ui` package, Rslib library build with declaration generation, CSS Modules, and React peer dependencies.
- `packages/react/src/components/{file-tree,file-preview,command-console,viking-bot}`: reserved view component entries; see the [module map](./packages/react/README.md#模块目录) and [component constraints](./packages/react/src/components/AGENTS.md). These entries have no component exports yet. `StatusBadge` is a scaffold example.
- `packages/react/src/pages`: page composition layer; see its [README](./packages/react/src/pages/README.md) and [constraints](./packages/react/src/pages/AGENTS.md). It currently contains documentation only.
- `packages/react/src/{hooks,utils}`: reserved internal shared code, without public subpaths. Module-private helpers stay within their module.
- `docs`: Rspress with `@rspress/plugin-preview`, external TSX demos, source viewing/copying, and hand-written props tables.
- `docs/docs/en` and `docs/docs/zh`: matching English and Simplified Chinese pages, with localized navigation.
- `docs/docs/_demos`: runnable examples using the public package exports.

Components receive prepared view data and state through props and report user intent through callbacks. The `pages/` layer combines components into complete pages, fetches and processes data, and passes the results to components. Routing and host navigation are supplied by the consuming application.

Library output is `packages/react/dist/`; documentation output is `docs/doc_build/`. Both development and production documentation consume the package exports without source aliases or TypeScript paths. `npm run check` builds the library first so documentation type-checking can resolve its declarations on a clean checkout. Import `@openviking/ui/style.css` once in applications; it resolves to `dist/index.css`. Rslib automatically externalizes React and ReactDOM through their peer dependency declarations. The package is private until a separate publication change establishes its release policy.

## Add a module

1. Implement the module in `packages/react/src/` and export it from its module entry. Add new public entries to the package exports and `rslib.config.ts` together. Export root-level APIs explicitly from `src/index.ts` when needed.
2. Keep module styling local and expose theme variables with the `--ov-ui-` prefix.
3. Add a demo in `docs/docs/_demos/` that imports from `@openviking/ui`.
4. Add matching documentation in `docs/docs/en/` and `docs/docs/zh/`, and update both locale sidebars in `docs/rspress.config.ts`. Reuse examples from `docs/docs/_demos/`.
5. Run the workspace checks and interact with the example in a browser.

## Continuous integration and deployment

The UI workflow checks relevant pull requests and pushes. The existing product documentation workflows also watch `ui/**`; they build both sites and copy this site's output into the product documentation's `ui/` directory before publishing.

- GitHub Pages: `/ui/` is part of the existing complete site artifact.
- TOS: `/ui/` is uploaded to the existing documentation bucket with the existing repository secrets.
- Domain, Pages environment rules, and TOS secrets remain repository administration settings.
- A branch push does not publish the production website. Production publication follows the existing `main` workflows.
- No npm publication is configured.

Do not deploy this output as a second independent GitHub Pages site in this repository: deploying a new artifact would replace the product documentation.

## License

The scaffold inherits the repository's AGPL-3.0-only license. See [LICENSE](../LICENSE).
