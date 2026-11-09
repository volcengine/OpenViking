# Icon sources

React imports these SVG files directly during its build.

- `svg/tui/copy.svg` — an original 16 × 16 outlined copy glyph drawn for this
  workspace. It inherits `currentColor`, follows the repository license and is
  bundled inline by Tui. The success state reuses `svg/scope-switcher/check.svg`.

- `svg/` — icons drawn for this workspace, following the repository license. The
  five full-colour file-type icons come from the design files.
- `svg/console/` — conversation, terminal, history and new-session controls
  drawn for this workspace in a consistent 1.2-unit outline. They inherit
  `currentColor`, follow this repository's license and are bundled inline.
  `history-empty.svg` is an original geometric tray illustration based on the
  supplied console screenshot. Its outline and background follow theme colors.
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
  `search.svg` is drawn for this workspace in the same 16 × 16 outline style.
  All three render as inline `svg` through `?react`, so they inherit
  `currentColor` and never appear as a scalable image element. They follow this
  repository's license and are bundled by ScopeSwitcher; no private IconBox
  dependency is used.
  The level icons (library / data space / user) are **not** shipped here: the
  component receives them from the host through `icon`, and the examples draw
  their own.
- `svg/file-type/` — the file-type icon set vendored from `@pierre/trees`
  (Apache-2.0, see that directory's `LICENSE.md`). One SVG per icon.
- `file-type-rules.json` — data rather than artwork: the file-name and extension
  rules that pick an icon, plus each icon's brand colour for light and dark
  backgrounds.
