# E terminal adaptation

The braille mark is rasterized from E's 24-grid polygons:
`2,21 6,21 18,3` and `9,21 21,3 21,21`. It uses 48 dot rows (12 character rows),
8×8 supersampling and 25% coverage so the fine triangular tip remains visible.
There are two columns of left padding and one blank row above and below.
The increasing ink area distinguishes the E taper from A's parallel stroke.

The artwork and accents use solid deep teal #0A7C93, readable on light and dark
terminals. The existing block-letter wordmark follows the terminal foreground.
No gradients, glow, changes to status meanings, or terminal-font overrides.
Terminals cannot embed the Geist web wordmark: glyphs and exact appearance are
controlled by the user's terminal font. Screenshots render actual ANSI output
on a fixed character grid (DejaVu Sans Mono), at 2× resolution.

Validation: wizard tests 89 passed, theme tests pass in the full suite. The full
suite has an existing help_ui timeout-option failure, reproduced on main.
The A proposal #5492 is preserved separately.
