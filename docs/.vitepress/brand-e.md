# OpenViking mark E

Selected variant: upright sail with a flat-tipped tapered slash. The Master
100-grid slash is `22,84 31,84 70,16.45 68.5,16.45`; the sail is
`37,84 76,16.45 76,84`. Small (24–63 px) widens only the slash to
`20.5,84 31,84 70,16.45 67,16.45`; Micro (≤23 px) widens it to
`19,84 31,84 70,16.45 65.5,16.45`. Favicons use Micro at 16 px and Small at
32/48 px, and switch to Paper in dark mode through favicon.svg.

Assets originate from the approved optimized variant E brand kit. Theme colors:
Ink #07090D, Paper #F2F4F6, deep teal #0A7C93 for the light favicon.

Variant A remains a separate draft option. This release does not close or
merge the existing A proposals (OpenViking #5491 and playground MR #106).
Existing article illustrations are editorial images and remain unchanged.

## Horizontal lockup audit

README and docs navigation use one outlined SVG rather than browser-typeset
OpenViking text. The wordmark is **Geist
SemiBold 600**, from the brand manual horizontal SVG: font size 84, text origin
(126, 86), tracking -2.52 (-0.03em), next to a 112-unit mark box. Kerning is baked
into the paths. The horizontal lockup uses the Small optical mark at its shipped
CSS size; large square and social assets use Master.

The 500.05 × 92.94 viewBox is scaled uniformly. The sail and its gap stay fixed
across optical sizes; only the slash widens to the left. Ink #07090D is used on
light surfaces and Paper #F2F4F6 on dark surfaces; no CSS filter or synthetic font.

The header logo is 136px wide (112px below 375px viewport width), matching the
website header so the wordmark stays balanced with the navigation labels.
The separate small `/ docs` label sits outside
the artwork and is hidden on mobile. Article typography remains independent;
#5543 cannot alter this wordmark. README PNGs are 800px wide, displayed at 300px.
Their absolute URLs keep PR previews and the next PyPI release working before
or after merge. Existing square-image URLs are retained and updated for external
consumers.

Studio/PWA, server/MCP, plugin and VikingBot icons use the matching E kit.
CLI has a separate terminal adaptation because terminals choose their own font.
