# Directory tour artwork

The six images, `step-1.webp` to `step-6.webp`, are the built-in illustrations of
the standard `DirectoryTour` targets, in this order: memories, sessions, personal
resources, shared resources, experiences and peers.

Original raster artwork from `frontend/docs/docs/public/directory-tour` is
resized to 432×144 pixels (steps 1–5) or 504×168 pixels (step 6) and losslessly
encoded as WebP. Steps 1, 2 and 5 use the refreshed original design artwork:
an outlined portrait, a filled lavender conversation bubble and hollow progress
nodes with an outlined completion badge. The build inlines the small files so
consumers need no separate asset hosting.

Display sizes are 212×70, 216×72, 223×72, 216×72, 216×72, and 252×84.
The 72px illustration viewport clips the peers artwork.

Matching `step-1-dark.webp` to `step-6-dark.webp` variants follow the host's
`html.dark` theme. They were generated with imagegen using the light images as
edit references, retaining their subjects, composition and lavender accents.
Steps 1, 2 and 5 were refreshed against both the corrected light artwork and
their prior dark variants so the portrait, bubble and progress details match
across themes. The shared prompt requests a charcoal `#1b1d21` background,
dark panel fills, soft light-gray outlines and subtle orbital lines in the
existing flat style. The dark files use the same pixel dimensions and lossless
WebP encoding. No design screenshots or temporary asset URLs are shipped.

To regenerate a light variant from its source PNG with `cwebp`, run:

```sh
cwebp -lossless -m 6 -resize 432 144 step-1.png -o step-1.webp
```

Keep each WebP below the library's 64 KB inline threshold.
