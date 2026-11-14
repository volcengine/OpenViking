# Icon sources

React imports these SVG files directly during its build. This file records where each asset comes from and under which license it ships. `svg/agent-connect/`, `svg/dashboards/`, `svg/file-preview/` and `svg/file-type/` also have their own README.

- `svg/command-console/cli-title.svg` — the first-party OpenViking CLI title artwork.
  The 245 × 40 viewBox, paths and cursor are unchanged; fills use `currentColor`
  for the host theme. CommandConsole bundles it inline.

- `svg/command-console/copy.svg` — an original 16 × 16 outlined copy glyph drawn for this
  workspace. It inherits `currentColor`, follows the repository license and is
  bundled inline by CommandConsole. The success state reuses `svg/scope-switcher/check.svg`.

- `svg/` — icons drawn for this workspace, following the repository license. The
  five full-colour file-type icons were supplied by the designer; see `svg/README.md`.
- `svg/console/conversation.svg` and `terminal.svg` — the exact artwork of
  `IconTwoBubblesChat` and `IconTerminalCode`, extracted from
  `@arco-design/iconbox-react-viking` 0.0.121 (ISC metadata).
  SVG paths, viewBox and strokes are unchanged; the React wrapper is removed.
- `svg/viking-bot/send.svg` and `attach.svg` — the exact `IconArrowDown` and
  `IconPlus2` artwork, from the same 0.0.121 package (ISC metadata). They retain
  their original paths and viewBox.
- `svg/console/history-empty.svg` is an original geometric tray illustration
  based on a supplied screenshot of the console. It follows this repository's license;
  its outline and background follow theme colors.
- `svg/empty/` — original empty-state illustrations drawn for this workspace in
  the style of `svg/file-browser/search-empty.svg`: the same 258 × 140 dashed
  grid, a `--ov-ui-color-border-3` shape behind and a `currentColor` shape in
  front. `folder.svg` is an empty folder, `chart.svg` a chart card,
  `people.svg` two people and `conversation.svg` two message bubbles. They
  follow the repository license and are bundled inline by the internal `Empty`
  primitive.
- `svg/arrow-right.svg` — the static artwork of `IconRightArrow`, which
  CommandConsole's quick commands render as `ArrowRightIcon`. Extracted from
  `@arco-design/iconbox-react-viking` 0.0.121, whose package metadata declares
  ISC; the same artwork is present in 0.0.134. The 14 × 14 viewBox, path and
  1.2-unit `currentColor` stroke are unchanged. Only the runtime wrapper is
  removed; the React package bundles the local SVG through SVGR.
- `svg/file-browser/` — the search dialog's filter glyph, the toolbar's refresh
  glyph and the empty search state's artwork. `search-filter.svg` and
  `refresh-cw-01.svg` keep the design's geometry with `currentColor` paint;
  `search-empty.svg` redraws the design's 258 × 140 dashed grid, document outline
  and magnifier in the style of `svg/console/history-empty.svg`, with its colours
  mapped to theme variables. See that directory's README.

- `svg/scope-switcher/` — the chevron and check assets were supplied by the
  designer. Chevron is 14 × 14 and check is 16 × 16. Their paint was changed from
  the design's fixed colours to `currentColor` so a host theme can recolour them;
  the paths are unmodified.
  `search.svg`, `space.svg` and `user.svg` are drawn for this workspace in the
  same 14/16 outline style. All of them render as inline `svg` through `?react`,
  so they inherit `currentColor` and never appear as a scalable image element.
  They follow this repository's license and are bundled by ScopeSwitcher; no
  private IconBox dependency is used.
  `space.svg` and `user.svg` are the default level icons exported as `SpaceIcon`
  and `UserIcon`. The library level icon has no glyph — the design draws a round
  gradient swatch picked by name — so `LibraryIcon` is implemented in
  `packages/react/src/icons/index.tsx` as a colour block instead of an SVG source
  here. Its five gradients live in `library-swatches.json` next to this file and
  are surfaced as `--ov-ui-library-swatch-1-default` … `-5-default` in
  `packages/react/src/theme.css`, so a host can override them through the public
  `--ov-ui-color-library-swatch-*` variables. That JSON is the single source: the
  React fallbacks import it directly and the CSS block is generated from it by
  `npm run sync:swatches`, so the two never drift. A host can still pass its own
  icons through `icon`; the examples reuse these built-in ones.
- `svg/file-browser/refresh-cw-01.svg` — the 14 × 14 refresh icon exported
  from the OpenViking Console design (Figma node `10674:69790`). The module
  uses the original artwork and applies the active theme's stroke color.
- `svg/file-type/` — the file-type icon set vendored from `@pierre/trees`
  (Apache-2.0, see that directory's `LICENSE.md`). One SVG per icon.
- `file-browser-empty-figma.png` — source artwork for the file browser's light empty state
- `empty-state.png` — transparent illustration for the file browser's dark empty state
  (258 × 140 at 2×). Supplied by the designer; the module imports it and the build
  inlines it as a data URI, so no separate asset file ships. It is a bitmap rather
  than an SVG because the original carries gradients and anti-aliased edges that a
  redraw would not match.

- `file-type-rules.json` — data rather than artwork: the file-name and extension
  rules that pick an icon, plus each icon's brand colour for light and dark
  backgrounds.

- The VikingBot welcome (lightbulb/book/video), report and session controls are
  original SVG artwork from `@arco-design/iconbox-react-viking` 0.0.134 (ISC).
  The history, new-session, session copy, copy success and locate glyphs use the
  original geometry. `new-session.svg` crops its 16 × 16 viewBox to the 13 × 13
  area around the glyph, so it fills its box like the tightly cropped
  `history.svg` and the two draw at the same size. React wrappers are removed and paint follows the host
  theme. No IconBox runtime is required.
- `svg/viking-bot/copy.svg` and `check.svg` — the reference OpenViking console's
  14px code and message action icons, from ConnectAgent's `connectAgentUi`.
  They follow this repository's license.

`svg/viking-bot/remove.svg` is an OpenViking-authored close glyph under the repository license.

- `svg/viking-bot/context-folder.svg` and `context-close.svg` — reference-tag
  artwork supplied from the OpenViking console source (`vikingbot/assets/`). They
  follow this repository's license. Paths, viewBox and stroke geometry are
  unchanged; fixed paint is replaced with `currentColor` to follow the host theme.
  File references reuse the file-type assets above.

- `svg/viking-bot/tool.svg` — unchanged `IconTool` artwork from the public MIT-licensed `@arco-design/web-react` 2.66.18.

- `svg/directory-tour/` — the folder and up-right arrow artwork supplied by the
  designer for the team experience introduction.
  The documentation demo uses `DirectoryTourFolderIcon` and `ArrowUpRightIcon`
  from `@openviking/react/icons`; the design's paths and colors are unchanged. They follow this repository's license.

- `svg/file-preview/` — toolbar, menu and warning artwork supplied by the designer
  (the menu and the deletion dialog designs). Paths and viewBoxes are
  retained; paints use `currentColor` for host theming. They follow the repository
  license and are bundled inline without a private icon runtime.
