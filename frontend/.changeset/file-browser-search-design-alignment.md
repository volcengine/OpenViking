---
'@openviking/react': patch
---

## en-US

Align the FileBrowser search dialog with the console design and drop the result preview panel it gained in 0.1.10.

- The empty state shows the design's artwork above the title and hint instead of two centred lines of text.
- Results fill the dialog width again: the hover/keyboard preview panel and its split layout are gone, and each row keeps its bottom divider, 24px icon tile and 14px name. `↑`/`↓` still move the current row and `Enter` opens it.
- The count line reads `N results ・ [mode] Vector retrieval Switch` in the design's order, and appears only when there are results; the empty, loading and failure states no longer carry it.
- The advanced panel takes the design's `bg-2` surface, and the mode menu its 259px popover with a 6px radius and card shadow.

BREAKING CHANGE: `FileBrowserLabels.searchPreview`, `searchPreviewEmpty` and `searchPreviewFailed` are removed, because the search dialog no longer previews results. Drop those overrides; the keys have no effect.

## zh-CN

将 FileBrowser 搜索弹窗对齐控制台设计，移除 0.1.10 中新增的结果预览面板。

- 空状态在标题和提示上方展示设计稿中的插画，替换原有的两行居中文字。
- 搜索结果恢复为占满弹窗宽度：移除悬停和键盘预览面板及其分栏布局，保留每行的底部分隔线、24px 图标块和 14px 名称。`↑` / `↓` 仍可切换当前行，`Enter` 打开结果。
- 结果统计行按设计顺序显示 `N results ・ [mode] Vector retrieval Switch`，仅在有结果时展示；空状态、加载和失败时不显示。
- 高级选项面板使用设计中的 `bg-2` 底色，模式菜单采用 259px 宽度、6px 圆角和卡片阴影。

破坏性变更：搜索弹窗不再预览结果，移除 `FileBrowserLabels.searchPreview`、`searchPreviewEmpty` 和 `searchPreviewFailed`。请删除这些文案覆盖，相关键不再生效。
