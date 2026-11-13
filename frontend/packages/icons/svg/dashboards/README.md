# Dashboard icons

SVG geometry comes from the OpenViking console implementation, authorized for use here.

- `experience-board.svg` and `wiki-board.svg`: `assets/context-console/dashboard.svg`; both products use the same layout glyph with contextual colour.
- `wiki-conflict-board.svg`: `pages/ContextManagement/components/assets/wiki-conflict-dashboard.svg`, including the original badge background, warning triangle, and punctuation.
- `wiki-book.svg`: `pages/ContextManagement/components/assets/wiki-book-open.svg`.
- `severity-filter.svg` and `filter-chevron.svg`: the same `WikiPreview/assets` directory, with original geometry.
- `warning.svg`: `pages/ContextManagement/components/ContextDetailPanel/WikiPreview/assets/warning.svg`.
- `file-link.svg`: the console's `@arco-design/iconbox-react-viking` `IconFileLinkV2ColorfulDm`, keeping the
  original path geometry. Colour tokens are replaced by `currentColor` so the host theme decides. It keeps all
  six paths: the sheet is one two-subpath `path` whose outer and inner contours fill into a hollow outline, so
  dropping either contour turns it into a solid block.

The console's `IconBarChart10`, `IconSourceControl` and `IconBeaker2` were extracted for the experience
dashboard's tabs, then dropped: that console screen renders the tab icons only in its standalone detail page,
never in the dashboard this component implements.

The `edit`, `success`, and `history` action SVGs come from the same `WikiPreview/assets` directory; `discuss` comes from its rendered conflict action.

The `file-input`, `scan-search`, and `activity` metric SVGs are extracted from the authorized console cases rendered by that implementation. They retain the source geometry and use `currentColor`.

Removed redundant global IDs and full-viewBox clip definitions; SVG viewports preserve clipping. Kept path geometry and stroke widths. Monochrome strokes/fills use `currentColor`; the conflict badge background uses a host-overridable surface variable. React wrappers ship through `@openviking/react/icons` under the repository AGPL-3.0-only license.

The dashboard dot texture in `../../dashboard-background.png` is copied from the same implementation's `WikiPreview/assets/background.png` and bundled by the stylesheet build.
