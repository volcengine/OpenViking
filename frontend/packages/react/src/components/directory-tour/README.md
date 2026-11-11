# DirectoryTour

Public entry: `@openviking/react/components/directory-tour`.
Styles: `@openviking/react/components/directory-tour/style.css`.

A browser-anchor walkthrough, independent from FileTree and business APIs.
`locateTarget(target, signal)` belongs to the host: load/expand the path, wait
for the row to render, and return its element. The tour handles scroll, highlight,
card placement, automatic skipping and cancellation. Missing/rejected/timed-out or removed
targets skip; pending targets stay hidden. All-missing sessions end with skipped IDs. It does not select files or persist
completion. Close/remount when changing account or scope.

`openVikingDirectoryTourSteps` is an optional six-step English preset. Translate
the steps; all six targets include bundled Figma artwork. Pass an illustration React node
to override it, or null to omit it. Root exports expose only the
component and its Props type; supporting types and the preset use the subpath.

Requires the browser Popover API for top-layer rendering; no arbitrary z-index,
modal mask, SVG, network dependency or internal asset URL is included.

See the bilingual documentation and FileTree example in
`frontend/docs/docs/{zh,en}/components/directory-tour.mdx`.

Optional `locale` (`zh-CN` or `en-US`) overrides the nearest scope; otherwise built-in controls inherit or fall back to `en-US`. `labels` overrides individual defaults; step content remains host-provided.
