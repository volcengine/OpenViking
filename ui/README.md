# OpenViking UI

An isolated npm workspace for reusable React modules and an Rspress component documentation site. The initial scaffold includes one example, `StatusBadge`; it does not migrate any existing business components.

## Development

Use Node.js 22.12+ (CI uses Node.js 22).

```bash
cd ui
npm ci
npm run dev
```

Open the `/ui/` URL printed by Rspress. Public package imports resolve to library source during documentation development, so an initial library build is not required.

```bash
npm run check          # Type-check the library, demos, and build configuration
npm run format:check   # Check workspace formatting
npm run build          # Build the ESM library, declarations, CSS, and static docs
npm run preview        # Serve the built documentation at /ui/
```

## Layout

- `packages/react`: private `@openviking/ui` package, Vite library build, CSS Modules, and React peer dependencies.
- `docs`: Rspress with `@rspress/plugin-preview`, external TSX demos, source viewing/copying, and hand-written props tables.
- `docs/docs/_demos`: runnable examples using the public package exports.

Library output is `packages/react/dist/`; documentation output is `docs/doc_build/`. Production documentation consumes the built package exports; only the development server aliases them to source. Import `@openviking/ui/style.css` once in applications. React is externalized from the library bundle. The package is private until a separate publication change establishes its release policy.

## Add a module

1. Implement the module in `packages/react/src/` and export it from `src/index.ts`.
2. Keep module styling local and expose theme variables with the `--ov-ui-` prefix.
3. Add a demo in `docs/docs/_demos/` that imports from `@openviking/ui`.
4. Add documentation and a sidebar entry in `docs/rspress.config.ts`.
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
