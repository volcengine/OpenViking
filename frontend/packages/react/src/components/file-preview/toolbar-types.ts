/** 内置操作的显示开关；省略时按可用能力显示。Omitted flags follow available capabilities. */
export interface FilePreviewToolbarOptions {
  /** @default true; shown when a path is available. */
  copyPath?: boolean;
  /** @default true; shown when the renderer supports both views. */
  viewSwitch?: boolean;
  /** @default true; shown when the active file/level provides editing. */
  edit?: boolean;
}
