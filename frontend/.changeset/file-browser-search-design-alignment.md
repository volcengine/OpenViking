---
'@openviking/react': patch
---

Align the FileBrowser search dialog with the console design and drop the result preview panel it gained in 0.1.10.

- The empty state shows the design's artwork above the title and hint instead of two centred lines of text.
- Results fill the dialog width again: the hover/keyboard preview panel and its split layout are gone, and each row keeps its bottom divider, 24px icon tile and 14px name. `↑`/`↓` still move the current row and `Enter` opens it.
- The count line reads `N results ・ [mode] Vector retrieval Switch` in the design's order, and appears only when there are results; the empty, loading and failure states no longer carry it.
- The advanced panel takes the design's `bg-2` surface, and the mode menu its 259px popover with a 6px radius and card shadow.

BREAKING CHANGE: `FileBrowserLabels.searchPreview`, `searchPreviewEmpty` and `searchPreviewFailed` are removed, because the search dialog no longer previews results. Drop those overrides; the keys have no effect.
