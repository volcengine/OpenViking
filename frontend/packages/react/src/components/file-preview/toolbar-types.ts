import type { ReactNode } from 'react';

/** 内置操作的显示开关；省略时按可用能力显示。Omitted flags follow available capabilities. */
export interface FilePreviewToolbarOptions {
  /** @default true; shown when the renderer supports both views. */
  viewSwitch?: boolean;
  /** @default true; shown when the active file/level provides editing. */
  edit?: boolean;
  /** @deprecated Only used by the legacy menuItems adapter. */
  copyContent?: boolean;
  /** @deprecated Only used by the legacy menuItems adapter. */
  download?: boolean;
}

/** 追加到更多菜单的命令。The host executes the command; the preview handles menu interaction. */
/** @deprecated Use FilePreviewAction with tab.actions. */
export interface FilePreviewMenuItem {
  key: string;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
  danger?: boolean;
  separatorBefore?: boolean;
  /** Rejections show an error and allow retrying. */
  onSelect: () => void | Promise<void>;
}
