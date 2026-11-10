# Internal Shared Code

English / [中文](README_CN.md)

Reusable internal code for this package's components, with **no public entry**. Nothing here is exported from the package root, from a subpath, or listed in `package.json` exports — a host cannot import it, and it carries no compatibility promise.

A shared piece is promoted here only when a second consumer actually needs it. What a single component uses stays inside that component's directory.

The internal `components/drawer` is an explicitly approved placement exception: it remains here as a generic right-side drawer, although `FileBrowserEntries` is currently its only direct consumer. The second-consumer rule continues to apply to other code. See the [Frontend Agent Guide](../../../../AGENTS.md).

Styles here are pulled in by the consuming component's own stylesheet, not by `components/style.css`: nothing public points at this directory, so the component that uses it is responsible for importing what it needs.

## Contents

- `components/drawer`: a right-side drawer with transitions, focus and scroll locking. It receives content through `children`; `file-browser-entries` supplies the business content and component-specific styles.
- `components/tooltip`: a positioned surface rendered into `document.body`, used by `file-preview` for the copy-path hint and the collapsed-level menu. It exists because an ancestor's `overflow: hidden` would clip an in-flow popover.
- `components/popover`: the white-card surface, also portalled into `document.body`, used by the file tree's node info and by the file preview.
- `components/divider`: a single rule whose extent and colour the caller passes inline.
- `components/resizable`: the draggable `ResizableHandle` divider, which uses pointer capture for the drag, supports the arrow keys, and keeps the width controlled by its caller. `file-browser` uses it to resize the directory tree.

## Boundaries

Shared code may not depend on `components/`, `modules/`, or any host-specific contract — the dependency runs one way, from the public components into this directory. A piece that needs business data, routing, or a host service belongs in `modules/` instead.

Development and build entry points are in the [UI workspace README](../../../../README.md); component conventions are in the [components guide](../components/AGENTS.md).
