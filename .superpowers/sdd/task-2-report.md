# Task 2 Report: AnyDocConverter core

## Status

Implemented the shared synchronous `AnyDocConverter` core on
`feat/anydoc-office-converter`. The implementation is intentionally not wired
into Word, PowerPoint, Excel, legacy DOC, or EPUB parsers.

## Delivered

- Added `AnydocConversionResult` and `AnyDocConverter.convert`.
- Added patchable `_load_document` using `anydoc.to_document`.
- Rejects PDF by suffix, format hint, or detected content with an error that
  directs callers to `PDFParser`.
- Serializes headings, paragraphs, lists, tables, block quotes, code blocks,
  rules, text styles, links, images, line breaks, note references, anchors, and
  document notes to GFM.
- Supports real firecrawl-anydoc list/table/link object shapes while retaining
  snake_case/camelCase tolerance through `attr` and `asset_id`.
- Saves only `image/*` assets through `storage.save_image`, naming them
  `image1`, `image2`, and so on, and writes paths relative to
  `storage.media_dir`.
- Preserves external images and emits alt text for unavailable or non-image
  embedded assets.

## TDD evidence

1. The brief's initial tests failed during collection because
   `anydoc_converter` did not exist.
2. The core implementation made the three required cases pass.
3. A regression test exposed duplicate image saves from table-cell block
   serialization (`images_saved == 2`); the serializer was corrected to render
   each cell block once.
4. Binding-shape tests failed for real anydoc `block.list` / `block.table.grid`
   containers and then passed after support was added.
5. A link-target test failed for the real `LinkTarget.value` shape and passed
   after target unwrapping was added.

## Verification

- `pytest tests/parse/test_anydoc_converter.py -v`
- `ruff check openviking/parse/parsers/anydoc_converter.py tests/parse/test_anydoc_converter.py`
- `ruff format --check openviking/parse/parsers/anydoc_converter.py tests/parse/test_anydoc_converter.py`
- Installed and imported `firecrawl-anydoc==0.1.8`.
- Live DOCX smoke with a heading and embedded PNG returned one saved image and
  Markdown containing `![image1](Demo/images/image1.png)`.

## Self-review

- Confirmed the implementation uses `to_document`, not `to_markdown`.
- Probed the installed binding's real Document, Block, Inline, Style, List,
  ListItem, Table, CellSlot, Cell, ImageSource, Asset, and LinkTarget fields.
- Confirmed non-image asset bytes are never passed to storage.
- Confirmed media links use POSIX separators and are relative to
  `storage.media_dir`.
- Confirmed no Task 3+ parser wiring or unrelated files are included.

## Concerns

- Full parser-suite integration is deferred by design to Task 3+.
- The targeted pytest run emits existing repository import/deprecation
  warnings; there are no failures from this task.
