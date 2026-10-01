# Shared reading fonts

These assets are copied byte-for-byte from OpenViking docs PR #5536, commit
`2cda522f4f894566508033e954ad530c7138a63d`, directory
`docs/.vitepress/theme/fonts/reading/`.

Chinese: LXGW WenKai GB Screen 1.522 (web family OV Reading Kai).
English prose: Adobe Source Sans 3. English headings: Adobe Source Serif 4.
Code: Maple Mono NF CN 7.9 (web family OV Code).

All four families use SIL OFL 1.1; the licenses and exact upstream sources and
hashes are included here. Modified subset families have distinct internal names;
original copyright and license metadata are preserved. Adobe files are unchanged.

Unicode subsets retain full source coverage, including uncommon Chinese and Nerd
Font symbols. The common-character grouping comes from the docs corpus; other
characters load their Unicode blocks on demand. No third-party font CDN is used.

For regeneration, use the pinned docs commit's
`docs/scripts/build-reading-fonts.py` and its source manifest, then synchronize
all generated font files and subsets.css together. Normal blog builds need no
font generation or network font download.
