# Icon sources

React imports these SVG files directly during its build.

- `svg/` — icons drawn for this workspace, following the repository license. The
  five full-colour file-type icons come from the design files.
- `svg/scope-switcher/` — the chevron and check assets are unmodified exports
  from the user-provided [OpenViking Console design](https://www.figma.com/design/ecpd1ZGHKRYibm1ODKEpM6/OpenViking-Console?node-id=10325-138037).
  Chevron: `I10325:138037;9343:425920` (14 × 14); check: `10325:138844`
  (16 × 16). They follow this repository's license and are bundled by
  ScopeSwitcher; no private IconBox dependency is used.
- `svg/file-type/` — the file-type icon set vendored from `@pierre/trees`
  (Apache-2.0, see that directory's `LICENSE.md`). One SVG per icon.
- `file-type-rules.json` — data rather than artwork: the file-name and extension
  rules that pick an icon, plus each icon's brand colour for light and dark
  backgrounds.
