import type { ReactElement, ReactNode, RefObject } from 'react';
import type { Locale } from '../../shared/locale.js';
import type {
  FileTreeEntryRow,
  FileTreeOperations,
  FileTreeProps,
} from '../file-tree/types.js';

/** 已选条目；独立于当前可见、分页或搜索结果。Selection independent of visible rows. */
export type ContextPickerEntry = Pick<
  FileTreeEntryRow,
  'uri' | 'name' | 'type'
>;

export interface ContextPickerLabels {
  title?: string;
  search?: string;
  selected?: string;
  unselected?: string;
  clearSearch?: string;
  selectedCount?: (count: number) => string;
  cancel?: string;
  confirm?: string;
  empty?: string;
}

export interface ContextPickerSlots {
  /** 左下角操作；省略时多选展示已选数量。Lower-left actions or the default count. */
  footerStart?: ReactNode;
  /** 树之后的补充内容，例如权限状态或上传面板。Content after the tree. */
  content?: ReactNode;
  /** 替换顶部已选条目。Replaces the selected-entry strip. */
  selection?: ReactNode;
  /** 包装确认按钮，例如增加说明浮层。Wraps the confirm button. */
  confirm?: (button: ReactElement) => ReactNode;
}

/** 受控的文件与目录选择弹窗；宿主提供数据并处理请求。Controlled presentation only. */
export interface ContextPickerProps extends Pick<
  FileTreeProps,
  | 'rows'
  | 'isReady'
  | 'loadingFallback'
  | 'errorFallback'
  | 'emptyFallback'
  | 'rootOrder'
  | 'renderRowIcon'
  | 'renderRowTooltip'
  | 'renderRowTrailing'
> {
  open: boolean;
  value: readonly ContextPickerEntry[];
  /** 多选为独立勾选，不关联父子节点。Independent checking without cascading. */
  multiple?: boolean;
  query?: string;
  operations?: Pick<FileTreeOperations, 'toggleExpand' | 'loadMore' | 'retry'>;
  /** 请求状态由宿主控制；确认期间阻止重复操作和关闭。Host-owned pending state. */
  loading?: boolean;
  confirming?: boolean;
  disabled?: boolean;
  confirmDisabled?: boolean;
  error?: ReactNode;
  /** 首次打开时自动展开、翻页并定位已选条目。Reveals the opening selection. */
  autoReveal?: boolean;
  /** 单选默认展示顶部已选条目。Defaults to true for single selection. */
  showSelection?: boolean;
  title?: string;
  triggerRef?: RefObject<HTMLElement | null>;
  locale?: Locale;
  className?: string;
  onChange?: (value: ContextPickerEntry[]) => void;
  onQueryChange?: (query: string) => void;
  onConfirm?: (value: readonly ContextPickerEntry[]) => void;
  onCancel?: () => void;
  onOpenEntry?: (entry: ContextPickerEntry) => void;
  slots?: ContextPickerSlots;
  labels?: ContextPickerLabels;
}
