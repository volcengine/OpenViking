# Reading typography

The documentation uses four OFL-licensed families, served from the docs origin:

| Role | Upstream font | Web family |
| --- | --- | --- |
| Chinese prose and headings | LXGW WenKai GB Screen 1.522 | `OV Reading Kai` |
| English prose and navigation | Adobe Source Sans 3 | `Source Sans 3` |
| English headings | Adobe Source Serif 4 | `Source Serif 4` |
| Inline code and code blocks | Maple Mono NF CN 7.9 | `OV Code` |

Source Sans precedes the Chinese face, so Latin letters in mixed-language prose
use proportional text glyphs. Source Serif is limited to headings. Maple is
limited to code and compact technical labels. Fonts are bundled with the site;
there are no runtime requests to Google Fonts, GitHub, or a third-party CDN.

## Choice and layout

Compared three variants on the same Chinese/English quick-start page:

- WenKai Screen + Source Sans prose / Source Serif headings: selected for its
  clear handwritten Chinese shapes and proportional, readable English prose.
- Noto Serif SC + Source Serif: more book-like; dense technical passages and
  small navigation labels feel more formal than the selected design.
- Noto Sans SC + Source Sans: easy to scan, but less of the handwriting quality
  requested for this site.

The reading theme uses 18px desktop / 17px mobile body text, a 760px maximum
article width, 1.85 line spacing, 14px navigation, and stronger paragraph
contrast. Tables and code keep their own denser sizing. Existing navigation,
search, code copying, dark mode, and document content are preserved.

These are visual judgments from the rendered comparisons, not a readability
study. Long prose, API tables, nested navigation, mobile, and dark mode should
be checked when changing typography again.

## Sources and licenses

Exact download URLs, versions, file hashes, and archive members are recorded
in `sources.json`. Each upstream license is included in this directory:

- [WenKai Screen](https://github.com/lxgw/LxgwWenKai-Screen): `wenkai-LICENSE.txt`.
- [Source Sans](https://github.com/adobe-fonts/source-sans): `source-sans-LICENSE.md`.
- [Source Serif](https://github.com/adobe-fonts/source-serif): `source-serif-LICENSE.md`.
- [Maple Mono](https://github.com/subframe7536/maple-font): `maple-LICENSE.txt`.

All four use SIL OFL 1.1. Retain these notices when redistributing the assets.
WenKai and Maple web subsets have distinct internal family/PostScript names;
this avoids using upstream reserved font names for modified fonts. Copyright,
authorship, and license metadata remain intact. The Adobe WOFF2 files are
unmodified upstream files. No Tsanger/仓耳 assets are used or included.

## Loading and regeneration

CJK and Nerd Font coverage is retained across Unicode-range subsets. Latin and
characters found in the current English/Chinese docs are separate hot subsets;
remaining characters live in bounded Unicode blocks. A browser requests only
blocks needed on a page. A new character does not require rebuilding fonts.
The same Maple assets serve Chinese code comments and Nerd Font symbols.

The generator validates source SHA-256 hashes and checks that every source
codepoint is assigned to one subset and that no requested glyph is lost.
OpenType layout features (including code ligatures) are retained. Browsers may
synthesize emphasis for the single-weight Chinese and code faces; Latin prose
and headings use real variable weights, and Latin prose has a real italic face.

Normal `npm run docs:build` uses the checked-in WOFF2 files and needs no Python
or network font download. To regenerate after an intentional font update:

1. Download the sources in `sources.json` into a scratch directory, extracting
   the listed Maple archive member. Use the `source_file` filenames.
2. In a Python environment with `fonttools==4.66.1` and `brotli==1.2.0`, run
   `python docs/scripts/build-reading-fonts.py --source-dir /path/to/originals`.
3. Rebuild docs and inspect English/Chinese desktop and mobile screenshots,
   actual browser-rendered font families, and font transfer sizes.
