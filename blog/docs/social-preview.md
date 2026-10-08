# Blog social preview

Approved direction: midnight navy sea viewed from above, with branching silver foam and ivory text. Generated with the built-in imagegen tool. The approved homepage card is the edit reference; keep its ocean, sail and wordmark while changing the section label and copy.

Asset: `blog/public/assets/covers/og-openviking-blog-0a798714.png`. Exported to 1200 × 630 PNG; the filename suffix is the first eight SHA-256 characters. Change the filename and all metadata references together when replacing the card. Keep previously published PNG URLs available for cached links.

The generated PNG is the source artwork. It replaces the earlier turquoise SVG composition. Review logo, spelling and legibility before accepting any regeneration.

The optimized E refresh removed only the old mark from a 100 × 110 crop, repaired the midnight-ocean texture with built-in imagegen, then overlaid the exact Master vector path in Paper #F2F4F6. The wordmark and every other part of the card remain unchanged.

## Optimized E background-repair prompt

```text
Use case: precise-object-edit. Asset type: tiny crop from an OpenViking blog social-preview ocean background. Remove only the white geometric sail logo from this crop and reconstruct the midnight-navy top-down ocean water and subtle dark wave texture behind it. Preserve the crop dimensions, camera angle, texture scale, colors, grain, and every pixel outside the removed logo area as closely as possible. Add no text, no logo, no symbols, no objects, no watermark.
```

## Final imagegen prompt

```text
Use case: text-localization. Edit the supplied approved OpenViking homepage social card into the requested sibling site's card.
Preserve the same midnight navy ocean field, fine rough water texture, silvery branching foam, dark quiet left, composition, 1.905:1 landscape aspect ratio, sail-logo geometry and bold OpenViking wordmark. Do not redesign or regenerate a different art direction. Make only the specified typography edits. The new small lowercase section label follows the existing wordmark on the same baseline, separated by a slash. It must be smaller and lighter than OpenViking, in muted ivory, and fit without touching the brightest foam. Logo, wordmark and main description remain warm ivory; secondary description and domain remain muted pale gray. No card frame, no page chrome, no selection number, no new images. Accurate crisp English text.
Section label: "/ blog". Replace "The context database for AI agents." with "Engineering notes from OpenViking". Replace "Memory, resources, and skills in one place." with "Context, memory, and AI agents." Replace bottom-left "openviking.ai" with "blog.openviking.ai". Keep all copy left-aligned at the original positions and similar font sizes. The final result contains exactly the approved sail logo, "OpenViking", "/ blog", "Engineering notes from OpenViking", "Context, memory, and AI agents.", and "blog.openviking.ai".
```
