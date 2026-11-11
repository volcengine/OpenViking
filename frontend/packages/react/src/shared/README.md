# Internal Shared Code

English / [中文](README_CN.md)

Reusable internal code for this package's components, with **no public entry**. Nothing here is exported from the package root, from a subpath, or listed in `package.json` exports — a host cannot import it, and it carries no compatibility promise.

A shared piece is promoted here only when a second consumer actually needs it. What a single component uses stays inside that component's directory.

The internal `components/drawer` and `components/modal` have explicitly approved placements. Drawer is used by `FileBrowserEntries`; Modal is used by node permissions. The second-consumer rule continues to apply to other code. See the [Frontend Agent Guide](../../../../AGENTS.md).

Styles here are pulled in by the consuming component's own stylesheet, not by `components/style.css`: nothing public points at this directory, so the component that uses it is responsible for importing what it needs.

## Contents

- `components/button`: trimmed Arco buttons with variants, sizes, icons, loading, native events and refs. Ordinary buttons do not submit forms by default. Shared by components, modules and docs; links, button groups and Provider coupling are omitted.
- `components/input`: trimmed Arco inputs and textareas with native events, form attributes, refs, sizes and decorative affixes. Password toggles, counters, clearing and automatic sizing are omitted.
- `components/modal`: an internal Arco-derived centered modal with transitions, focus and shared scroll locking. It has no SDK export. Internal tests use a private built JS entry excluded from the npm package.
- `components/drawer`: a right-side drawer with transitions, focus and scroll locking. It receives content through `children`; `file-browser-entries` supplies the business content and component-specific styles.
- `components/tooltip`: a positioned surface rendered into `document.body`, used by `file-preview` for the copy-path hint and the collapsed-level menu. It exists because an ancestor's `overflow: hidden` would clip an in-flow popover.
- `components/popover`: the white-card surface, also portalled into `document.body`, used by the file tree's node info and by the file preview.
- `components/divider`: a single rule whose extent and colour the caller passes inline.
- `components/resizable`: the draggable `ResizableHandle` divider, which uses pointer capture for the drag, supports the arrow keys, and keeps the width controlled by its caller. `file-browser` uses it to resize the directory tree.

## Boundaries

Input and Button reuse only Arco's interaction logic. Visual rules come from the console's `@arco-design/theme-ve-o-aml-design@0.0.36` (declared MIT), specifically `css/arco.css`, `theme.css` and the relevant `components/Input/index.less` overrides. They do not use Arco's default skin. The dependency and `assets/styles/aml-design.less` were verified against OpenViking in vikingdb-fe master `190844896665bc3229e03423114803d24b4faa82`.

The theme uses 6px corners and 13px text by default, 500-weight buttons, outlined white inputs and a 2px input focus halo. Sizes remain 24/28/32/36px. Application overrides such as the file-tree toolbar and scope search stay with their consumers. Colors are mapped in `src/theme.css` and remain overridable through `--ov-ui-color-*`; no full theme stylesheet, global reset, private icon or font asset is imported.

Shared code may not depend on `components/`, `modules/`, or any host-specific contract — the dependency runs one way, from the public components into this directory. A piece that needs business data, routing, or a host service belongs in `modules/` instead.

Docs consume a separate private build through `docs/internal-controls.ts`; these entries are excluded from the npm package. The `#demo-controls` import in examples is only for this site. Hosts replace it with their own buttons.

Development and build entry points are in the [UI workspace README](../../../../README.md); component conventions are in the [components guide](../components/AGENTS.md).
