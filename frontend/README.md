# OpenViking Frontend

React foundations for building OpenViking AI application interfaces: Rslib builds, bilingual Rspress documentation, layered directory conventions, and scoped styles. FileTree and FilePreview provide placeholder views, alongside five file-type icons. Four business module placeholders let applications integrate their layout before business Hooks and interactions land.

Agent-facing development constraints are documented in [AGENTS.md](./AGENTS.md).

## Development

Use Node.js 22.12+ (CI uses Node.js 22).

```bash
cd frontend
npm ci
npm run dev
```

Open the `/ui/` URL printed by Rspress. The official workspace development plugin starts the library's Rslib watcher before the documentation server, so no manual library build is required. Demos consume the built package exports and update when the watcher rebuilds them.

The navigation language menu switches between English at `/ui/` and Simplified Chinese at `/ui/zh/`. Existing English URLs remain unchanged.

```bash
npm run lint           # Check formatting, lint rules, and types for all workspaces
npm run lint:fix       # Format files and apply available lint fixes
npm run typecheck      # Check types without formatting or lint rules
npm test               # Verify built public JS, declaration, and CSS entries
npm run build          # Build the ESM library, declarations, CSS, and static docs
npm run preview        # Serve the built documentation at /ui/
```

## Layout

- `packages/react`: `@openviking/react` package, Rslib library build with declaration generation, `ov-` prefixed BEM class names, and React peer dependencies.
- `packages/react/src/components/{file-tree,file-preview,tui,scope-switcher,agent-connect}`: reusable views with public props types. All five ship placeholder views and will be rewritten against their reviewed contracts; file-tree and file-preview have their shell and interaction in place, while tui, scope-switcher and agent-connect render a fixed-size shell only. See the [module map](./packages/react/README.md#module-map).
- `packages/react/src/modules/{file-browser,console,task-center}`: importable placeholder components with settled public Props / Labels / Slots types, and styles. Business Hooks and interactions are planned.
- `packages/react/src/client`: the framework-neutral request client, exported from `@openviking/react/client` — a `fetch`-based `createOvClient` plus the contract modules depend on (`OvClient`, `OvRequestOptions`, `OvClientError`).
- `packages/react/src/{hooks,utils}`: reserved internal shared code, without public subpaths. Module-private helpers stay within their module.
- `docs`: Rspress with `@rspress/plugin-preview`, external TSX demos, source viewing/copying, and hand-written props tables.
- `docs/docs/en` and `docs/docs/zh`: matching English and Simplified Chinese pages, with localized navigation.
- `docs/docs/_demos`: runnable examples using the public package exports.

Prettier checks formatting; Rslint checks code quality and TypeScript types in one run. `npm run lint`, `npm run lint:fix`, and `npm run typecheck` build the library first so documentation examples can resolve its declarations on a clean checkout. `npm run check` is an alias for `lint`. The standalone `typecheck` uses TypeScript's `tsc --noEmit` for the library, build configuration, and documentation projects. TypeScript also generates the library declarations.

Pass files, directories, or quoted globs after `--` to limit formatting and lint rules. Without arguments, `lint` and `lint:fix` check the whole frontend workspace. Type checking always covers the complete configured TypeScript projects, including when lint targets specific files. Type errors require manual correction.

JavaScript and TypeScript files, including JSX and TSX, are limited to 200 lines per file, excluding blank and comment-only lines. Exceeding this limit fails lint.

```bash
npm run lint -- packages/react/src/index.ts
npm run lint -- packages/react/src/index.ts docs/rspress.config.ts
npm run lint:fix -- 'docs/docs/_demos/**/*.tsx'
```

Library output is `packages/react/dist/`; documentation output is `docs/doc_build/`. Both development and production documentation consume the package exports without source aliases or TypeScript paths. Import `@openviking/react/style.css`, a category's `components/style.css` or `modules/style.css`, or a specific `<category>/<module>/style.css`; each is complete for its scope. JS imports do not load CSS. The full stylesheet resolves to `dist/style.css`. There are no category-level `components` or `modules` JS exports. Rslib automatically externalizes React and ReactDOM through their peer dependency declarations. Publication is controlled by the separate release workflow and is not part of local validation.

## Target layout

The workspace currently contains React components, shared SVGs, three business module placeholders, the request client, and documentation. The target also includes framework-neutral libraries, the Client transport, Vue bindings, and deployable micro-apps. Add each planned directory with its first implementation.

```text
frontend/
├── packages/
│   ├── icons/               # SVG sources, provenance and licensing
│   ├── tokens/              # Framework-neutral theme values
│   ├── lib/                 # Framework-neutral functions and resource types
│   ├── client/              # Framework-neutral request client, to be added with its first implementation
│   ├── react/               # @openviking/react
│   │   └── src/
│   │       ├── components/
│   │       ├── icons/       # @openviking/react/icons
│   │       └── modules/     # Business Hooks and composed views
│   └── vue/                 # Future @openviking/vue
├── apps/
│   └── workbench-embed/     # Deployable microfrontend entry
├── examples/               # Standalone examples consuming built packages
└── docs/                   # Rspress documentation
```

Business modules will combine data-loading Hooks with view components around a focused capability. Hooks will expose data, state, and actions separately from the composed view, so hosts can reuse the logic with their own UI. The shared Client lives in the React package and is exported from `@openviking/react/client`; it is a `fetch`-based client over the native OpenViking API with no React dependency. A React Provider will supply request defaults, with optional module / Hook overrides; nonstandard gateways are adapted once at the host request boundary. Hosts retain authentication, account scope, routing, permissions, and product-specific flows. Complete pages belong to hosts, including Web Studio and micro-apps. The [module boundaries and request configuration](./packages/react/src/modules/README.md) focus on the personal terminal; administration is outside the current scope. Each `@openviking/react/modules/<module>` entry currently exports a placeholder component, its settled Props type and its subpath-only Labels and Slots types. Hooks and the Provider are not exported yet. Module CSS must include its component dependencies as implementations land; Hook-only use will need no CSS.

Shared source directories can supply multiple framework builds. The `lib/` directory is a source boundary. React-only consumers receive the shared code through the React package. When standalone consumers need those functions, expose a framework-neutral package and verify installation in a project without React or Vue. React icon components use the shared SVG sources and ship with the React package, including their type declarations. Import icons from `@openviking/react/icons`. Web Studio consumes these assets through package exports from its separate application directory.

## Add a module

1. Implement the component or business module in `packages/react/src/` and export it from its entry. Business modules expose their Hooks and composed views separately from `modules/<module>`. Add new public JS and CSS entries to the package exports and `rslib.config.ts` together. Include module styles in the relevant aggregate stylesheet; composed views must cover their component dependencies. Export root-level APIs explicitly from `src/index.ts` when needed.
2. Keep module styling local and expose theme variables with the `--ov-ui-` prefix.
3. Add a demo in `docs/docs/_demos/` that imports from `@openviking/react`.
4. Add matching documentation in `docs/docs/en/` and `docs/docs/zh/`, and update both locale sidebars in `docs/rspress.config.ts`. Reuse examples from `docs/docs/_demos/`.
5. Run the workspace checks and interact with the example in a browser.

## Continuous integration and deployment

The UI workflow checks relevant pull requests and pushes. The existing product documentation workflows also watch `frontend/**`; they build both sites and copy this site's output into the product documentation's `ui/` directory before publishing.

- GitHub Pages: `/ui/` is part of the existing complete site artifact.
- TOS: `/ui/` is uploaded to the existing documentation bucket with the existing repository secrets.
- Domain, Pages environment rules, and TOS secrets remain repository administration settings.
- A branch push does not publish the production website. Production publication follows the existing `main` workflows.
- npm publication uses the separate tag-triggered release workflow; local validation does not publish a package.

Do not deploy this output as a second independent GitHub Pages site in this repository: deploying a new artifact would replace the product documentation.

## License

The scaffold inherits the repository's AGPL-3.0-only license. See [LICENSE](../LICENSE).
