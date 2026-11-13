import type { HTMLAttributes, KeyboardEvent, ReactNode } from 'react';

import type {
  FileTreeEntryRow,
  FileTreeErrorRow,
  FileTreeInfoLabels,
  FileTreeLabels,
  FileTreeLoadMoreRow,
  FileTreeRow,
} from './types.js';

/**
 * 文案的内部形状：标量项都有默认值，嵌套的 `info` 保持可选。
 *
 * Internal copy shape: every scalar has a default, while the nested `info`
 * stays optional.
 */
export type ResolvedLabels = Required<Omit<FileTreeLabels, 'info'>> &
  Pick<FileTreeLabels, 'info'>;

/** 渲染行所需的外部输入，由组件主体注入。Row rendering inputs, injected by the tree. */
export interface FileTreeRowContext {
  /** 已合并默认值的文案。Copy with defaults merged in. */
  text: ResolvedLabels;
  /** 目录说明的宿主覆盖，用于解析内置说明。Host overrides for directory copy. */
  infoLabels?: FileTreeInfoLabels;
  /** 当前选中的行 id。Currently selected row id. */
  selectedId?: string | null;
  /** 当前展示浮层的行 id。Row whose tooltip is showing. */
  /** 为行追加属性。Extra attributes for a row. */
  getRowAttributes?: (row: FileTreeRow) => HTMLAttributes<HTMLDivElement>;
  /** 行尾标记。Row-end marker. */
  renderRowTrailing?: (row: FileTreeEntryRow) => ReactNode;
  /** 悬停浮层内容。Hover content. */
  renderRowTooltip?: (row: FileTreeEntryRow) => ReactNode;
  /** 行首文件图标。Leading icon for a file row. */
  renderRowIcon?: (row: FileTreeEntryRow) => ReactNode;
  /** 加载转圈；省略时用内置的 CSS 圆环。Loading spinner; defaults to the CSS ring. */
  renderSpinner?: () => ReactNode;
  /** 选中。Selects a row. */
  onSelect?: (row: FileTreeEntryRow) => void;
  /** 展开或折叠。Expands or collapses a directory. */
  onToggleExpand?: (row: FileTreeEntryRow) => void;
  /** 加载下一页。Loads the next page. */
  onLoadMore?: (row: FileTreeLoadMoreRow) => void;
  /** 重试失败的子节点。Retries a failed children load. */
  onRetry?: (row: FileTreeErrorRow) => void;
  /** 打开保留策略设置。Opens retention settings. */
  onOpenRetentionSettings?: (uri: string) => void;
  /** 行的键盘处理。Key handling for a row. */
  onRowKeyDown: (
    event: KeyboardEvent<HTMLDivElement>,
    row: FileTreeEntryRow,
  ) => void;
  /** 指针进入行。Pointer enters a row. */
  /** 指针离开行。Pointer leaves a row. */
}
