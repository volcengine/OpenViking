# Dashboard icons

SVG geometry comes from the user-authorized Volcano Engine console implementation at `release/openviking/2026-11-12`, commit `4a929404299ed095d542ad4ea573e9d590cbd14b`.

- `experience-board.svg` and `wiki-board.svg`: `assets/context-console/dashboard.svg`; both products use the same layout glyph with contextual colour.
- `wiki-conflict-board.svg`: `pages/ContextManagement/components/assets/wiki-conflict-dashboard.svg`, including the original badge background, warning triangle, and punctuation. The demo formerly painted these layers in the wrong colours.
- `wiki-book.svg`: `pages/ContextManagement/components/assets/wiki-book-open.svg`.
- `severity-filter.svg` and `filter-chevron.svg`: the same `WikiPreview/assets` directory, with original geometry.
- `warning.svg`: `pages/ContextManagement/components/ContextDetailPanel/WikiPreview/assets/warning.svg`.

The `edit`, `success`, and `history` action SVGs come from the same `WikiPreview/assets` directory; `discuss` comes from its rendered conflict action.

The `file-input`, `scan-search`, and `activity` metric SVGs are extracted from the authorized console cases rendered by that release. They retain the source geometry and use `currentColor`.

Removed redundant global IDs and full-viewBox clip definitions; SVG viewports preserve clipping. Kept path geometry and stroke widths. Monochrome strokes/fills use `currentColor`; the conflict badge background uses a host-overridable surface variable. React wrappers ship through `@openviking/react/icons` under the repository AGPL-3.0-only license.

The dashboard dot texture in `../../dashboard-background.png` is copied from the same release's `WikiPreview/assets/background.png` and bundled by the stylesheet build.
