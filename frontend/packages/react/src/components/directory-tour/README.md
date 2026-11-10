# DirectoryTour

Public entry: `@openviking/react/components/directory-tour`.
Styles: `@openviking/react/components/directory-tour/style.css`.

A browser-anchor walkthrough, independent from FileTree and business APIs.
`locateTarget(target, signal)` belongs to the host: load/expand the path, wait
for the row to render, and return its element. The tour handles scroll, highlight,
card placement, retry/skip and cancellation. It does not select files or persist
completion. Close/remount when changing account or scope.

`openVikingDirectoryTourSteps` is an optional six-step English preset. Translate
the steps and pass illustrations as React nodes. Root exports expose only the
component and its Props type; supporting types and the preset use the subpath.

Requires the browser Popover API for top-layer rendering; no arbitrary z-index,
modal mask, SVG, network dependency or internal asset URL is included.

See the bilingual documentation and FileTree example in
`frontend/docs/docs/{zh,en}/components/directory-tour.mdx`.
