# File browser icons

- `search-filter.svg` comes from the supplied `Frame.svg`. The 28 × 28
  viewBox and paths are unchanged. Its stroke inherits the button's theme color.
  The artwork follows the repository license.
- `refresh-cw-01.svg` is the console's refresh glyph. The 14 × 14 viewBox and
  paths are unchanged; its stroke inherits the button's theme color.
- `search-empty.svg` is the empty search state's artwork, redrawn in the style of
  `svg/console/history-empty.svg`: the same 258 × 140 canvas, document outline,
  fold and magnifier geometry, but the design's five per-row grid paths collapsed
  into one dashed grid masked by a radial fade, and the white backdrop dropped so
  the dialog's own surface shows through. The design's fixed colours become theme
  variables: `--ov-ui-color-border-2` for the grid, `--ov-ui-color-border-3` for
  the sheet behind, `currentColor` for the front sheet and glyph.
