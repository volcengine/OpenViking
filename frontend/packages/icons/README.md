# Icon sources

React imports these SVG files directly during its build.

- `svg/tui/copy.svg` — an original 16 × 16 outlined copy glyph drawn for this
  workspace. It inherits `currentColor`, follows the repository license and is
  bundled inline by Tui. The success state reuses `svg/scope-switcher/check.svg`.

- `svg/` — icons drawn for this workspace, following the repository license. The
  five full-colour file-type icons come from the design files.
- `svg/console/conversation.svg` and `terminal.svg` — the exact artwork of
  `IconTwoBubblesChat` and `IconTerminalCode` used by the Volcengine console.
  Extracted from `@arco-design/iconbox-react-viking` 0.0.121 (ISC metadata).
  SVG paths, viewBox and strokes are unchanged; the React wrapper is removed.
- `svg/viking-bot/send.svg` and `attach.svg` — the exact `IconArrowDown` and
  `IconPlus2` artwork used by the Volcengine VikingBot composer, from the same
  0.0.121 package (ISC metadata). They retain their original paths and viewBox.
- `svg/console/history-empty.svg` is an original geometric tray illustration
  based on the supplied console screenshot. It follows this repository's license;
  its outline and background follow theme colors.
- `svg/arrow-right.svg` — the static artwork of `IconRightArrow` used by the
  Volcengine OpenViking console's `TerminalBody` quick commands. Extracted from
  `@arco-design/iconbox-react-viking` 0.0.121, whose package metadata declares
  ISC; the same artwork is present in 0.0.134. The 14 × 14 viewBox, path and
  1.2-unit `currentColor` stroke are unchanged. Only the runtime wrapper is
  removed; the React package bundles the local SVG through SVGR.
- `svg/scope-switcher/` — the chevron and check assets come from the
  user-provided [OpenViking Console design](https://www.figma.com/design/ecpd1ZGHKRYibm1ODKEpM6/OpenViking-Console?node-id=10325-138037).
  Chevron: `I10325:138037;9343:425920` (14 × 14); check: `10325:138844`
  (16 × 16). Their paint was changed from the design's fixed colours to
  `currentColor` so a host theme can recolour them; the paths are unmodified.
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
- `svg/file-type/` — the file-type icon set vendored from `@pierre/trees`
  (Apache-2.0, see that directory's `LICENSE.md`). One SVG per icon.
- `empty-state.png` — the empty-state illustration for the file browser
  (258 × 140 at 2×). Supplied by the design; the module imports it and the build
  inlines it as a data URI, so no separate asset file ships. It is a bitmap rather
  than an SVG because the original carries gradients and anti-aliased edges that a
  redraw would not match.

- `file-type-rules.json` — data rather than artwork: the file-name and extension
  rules that pick an icon, plus each icon's brand colour for light and dark
  backgrounds.

- The VikingBot welcome (lightbulb/book/video), report and session controls are
  original SVG artwork from `@arco-design/iconbox-react-viking` 0.0.134 (ISC),
  used by `release/openviking/2026-11-12` at `3dbb7543f`. Paths remain unchanged;
  React wrappers are removed. The new-session viewBox is cropped to match the
  history icon's visual size. Both Console modes share these session-control
  assets. No IconBox runtime is required.

`viking-bot/remove.svg` is an OpenViking-authored close glyph under the repository license.

- `svg/viking-bot/context-folder.svg` and `context-close.svg` — reference-tag
  artwork from the user-supplied OpenViking console source at
  `release/openviking/2026-11-12`, commit `602a7382d`, under
  `vikingbot/assets/`. They follow this repository's license. Paths, viewBox and
  stroke geometry are unchanged; fixed paint is replaced with `currentColor`
  to follow the host theme. File references reuse the file-type assets above.

- `svg/viking-bot/tool.svg` — unchanged `IconTool` artwork from the public MIT-licensed `@arco-design/web-react` 2.66.18, matching the tool row in `vikingdb-fe` release/openviking/2026-11-12.

- `svg/directory-tour/` — the folder and up-right arrow artwork from the
  [OpenViking Console team experience introduction](https://www.figma.com/design/ecpd1ZGHKRYibm1ODKEpM6/OpenViking-Console?node-id=10168-334211).
  The documentation demo uses `DirectoryTourFolderIcon` and `ArrowUpRightIcon`
  from `@openviking/react/icons`; the design's paths and colors are unchanged. They follow this repository's license.

- `svg/file-preview/` — toolbar, menu and warning artwork from the user-provided
  [personal workbench and open-source design](https://www.figma.com/design/ecpd1ZGHKRYibm1ODKEpM6/OpenViking-Console?node-id=10295-379152),
  menu `10438:19513` and deletion dialog `10295:380365`. Paths and viewBoxes are
  retained; paints use `currentColor` for host theming. They follow the repository
  license and are bundled inline without a private icon runtime.
