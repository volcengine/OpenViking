# Brand E horizontal lockup

The topbar uses the same outlined Geist SemiBold 600 horizontal logo as docs and
README. It no longer typesets OpenViking with the article heading font. Paths
follow the brand manual horizontal SVG: 84px Geist 600, -0.03em tracking, text
origin (126,86), 112px mark box; the slash is the approved E variant.

Both themes have identical proportions: Ink #07090D on light, Paper #F4F5F3 on
dark, no filters or rounded masks. A 500.05 × 92.94 viewBox includes at least 10
primary-grid units of clear space. The slide caption and actual SVG disagree on
the ink gap; these assets follow the displayed SVG (39.725 primary-grid units).

CSS scales the complete artwork uniformly to 136px wide (112px below 375px
viewport width), matching the website and docs headers. The brand link keeps a
minimum height of 36px. The separate small
`/ blog` label is hidden on mobile. Reading fonts remain independent, including
the pending #5542 typography update. The brand wordmark never inherits them.

Production build and 30 existing tests pass. Screenshots cover light/dark at
1440, 390 and 320px, with loaded artwork and no horizontal page overflow.
