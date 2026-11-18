export interface FilePreviewLabels {
  previewImage?: string;
  closePreview?: string;
  expandVideo?: string;
  videoTranscript?: string;
  videoFrames?: (count: number) => string;
  videoSpeaker?: string;
  videoNoTranscript?: string;
  videoNoFrames?: string;
  /** 展开折叠路径层级的无障碍名称。Accessible name for hidden path levels. */
  showHiddenPath?: string;
  copyPath?: string;
  copied?: string;
  copyFailed?: string;
  moreActions?: string;
  actionFailed?: string;
  previewMode?: string;
  sourceMode?: string;
  viewSwitch?: string;
  edit?: string;
  editFile?: string;
  save?: string;
  /** 编辑区文本框的无障碍名称。Accessible name of the editor. */
  editor?: string;
  /** 编辑器加载失败时的提示。Message shown when the editor fails to load. */
  editorLoadFailed?: string;
  /** 语法高亮加载失败时的提示。Message shown when syntax highlighting fails to load. */
  highlightFailed?: string;
  /** 读取失败后退出编辑态的按钮文案。Label of the button that leaves editing after a failed load. */
  leaveEditing?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
  /** 读取失败时的提示。Message shown when reading failed. */
  loadFailed?: string;
  /** 保存失败时的提示。Message shown when saving failed. */
  saveFailed?: string;
  versionNoteTitle?: string;
  versionNoteLabel?: string;
  versionNotePlaceholder?: string;
  versionNoteCancel?: string;
  versionNoteConfirm?: string;
  versionNotCreated?: string;
  /** 无法预览该格式时的提示。Message shown when the format cannot be previewed. */
  unsupported?: string;
  /** 取回正文时的加载提示。Message shown while the text is being fetched. */
  loading?: string;
  /** 文件长度为 0 时的提示。Message shown when the file itself is empty. */
  fileEmpty?: string;
  /** 文件非空但没有可展示内容时的提示。Message shown for a non-empty file with nothing to render. */
  noContent?: string;
  /** 正文被截断时的提示。Message shown when the body was truncated. */
  truncated?: string;
}
