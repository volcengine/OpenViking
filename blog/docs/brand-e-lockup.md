# Brand E horizontal lockup

The topbar uses the same outlined Geist SemiBold 600 horizontal logo as docs and
README. It no longer typesets OpenViking with the article heading font. Paths
follow the brand manual horizontal SVG: 84px Geist 600, -0.03em tracking, text
origin (126,86), 112px mark box; the mark uses the optimized Small E geometry.

Both themes have identical proportions: Ink #07090D on light, Paper #F2F4F6 on
dark, no filters or rounded masks. The Small mark uses a flat-tipped slash
`20.5,84 31,84 70,16.45 67,16.45` and sail
`37,84 76,16.45 76,84`. A 500.05 × 92.94 viewBox preserves the outlined Geist
wordmark and its spacing.

CSS scales the complete artwork uniformly to 136px wide (112px below 375px
viewport width), matching the website and docs headers. The brand link keeps a
minimum height of 36px. The separate small
`/ blog` label is hidden on mobile. Reading fonts remain independent, including
the pending #5542 typography update. The brand wordmark never inherits them.

Master geometry is used for square and social assets; Micro is used at 16px.
Previously published content-hashed social assets remain available for caches.
