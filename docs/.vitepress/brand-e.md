# OpenViking mark E

Selected variant: upright sail with a tapered slash. The primary 100-grid slash
is `22,84 30,84 70,16`; sail `36,84 76,16 76,84`. At small navigation sizes the
24-grid slash is `2,21 6,21 18,3`; sail `9,21 21,3 21,21`. Favicons use the
16-grid mark and switch to Paper in dark mode through favicon.svg.

Assets originate from the approved variant E brand kit. Theme colors: Ink
#07090D, Paper #F4F5F3, deep teal #0A7C93 for the light favicon.

Variant A remains a separate draft option. This release does not close or
merge the existing A proposals (OpenViking #5491 and playground MR #106).
Existing article illustrations are editorial images and remain unchanged.

## Horizontal lockup audit

README and docs navigation use one outlined SVG rather than browser-typeset
OpenViking text. The blog uses byte-identical assets. The wordmark is **Geist
SemiBold 600**, from the brand manual horizontal SVG: font size 84, text origin
(126, 86), tracking -2.52 (-0.03em), next to a 112-unit mark box. Kerning is baked
into the paths. Only the approved E tapered slash replaces the original mark.

The slide caption and its SVG disagree about the mark-to-wordmark gap. These
assets follow the actual displayed SVG (39.725 primary-grid units between ink).
The E slash's wider base is included when retaining 10 units of clear space on
all sides. The 500.05 × 92.94 viewBox is scaled uniformly. Ink #07090D is used on
light surfaces and Paper #F4F5F3 on dark surfaces; no CSS filter or synthetic font.

The header logo is 136px wide (112px below 375px viewport width), matching the
website header so the wordmark stays balanced with the navigation labels.
The separate small `/ docs` label sits outside
the artwork and is hidden on mobile. Article typography remains independent;
#5543 cannot alter this wordmark. README PNGs are 800px wide, displayed at 300px.
Their absolute URLs pin the asset commit so PR previews and the next PyPI
release both work before or after merge. Existing
square-image URLs are retained and updated for external consumers.

Studio/PWA, server/MCP, plugin and VikingBot icons use the matching E kit.
CLI has a separate terminal adaptation because terminals choose their own font.
