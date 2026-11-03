# Typography matching t0saki.com

Body: IBM Plex Sans + LXGW Neo XiHei Screen Full 1.239.
Headings: Source Serif 4 + Noto Serif SC (700).
Code remains Maple Mono NF CN as requested previously.

The WOFF2 bytes are unchanged copies of the public t0saki.com font assets.
Sources and SHA-256 hashes are in sources.json. The CSS is adapted to local URLs;
no font program is renamed, modified or subsetted again. The original subset
family names, copyright and license metadata remain intact. No runtime requests
to t0saki.com or a font CDN occur.

XiHei uses IPA Font License 1.0, with incorporated fonts' licenses also included.
IBM Plex Sans, Noto Serif SC, Source Serif 4 and Maple use SIL OFL 1.1.

The XiHei faces prefer locally installed IPAexGothic, allowing a reader to use
the original IPA font instead. The public font-licenses.html page links the IPA
license, original font download and installation/reload instructions. This follows
the upstream embedding guide's local() option:
https://github.com/lxgw/lxgw/blob/main/documents/xizhi_embedding_instructions.md

Only typography and its required license/replacement information change; the
site layout, branding and language/theme controls are preserved.
