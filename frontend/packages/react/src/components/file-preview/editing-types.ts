import type { FilePreviewSource } from './types.js';

/** A text anchor and explanation shown on matching lines in the editor. */
export interface FilePreviewEditorAnnotation {
  readonly matchText: string;
  readonly description: string;
  readonly reason?: string;
}

export interface FilePreviewEditorDocument {
  content: string;
  annotations?: readonly FilePreviewEditorAnnotation[];
  /** Text displayed at the end of the editor, outside the editable document. */
  readOnlySuffix?: string;
}

/**
 * 文件编辑配置。省略时只读；可提供编辑所需的完整文本，并处理保存与取消操作。
 * File-editing options. Omit them for a read-only preview. Provide the full text
 * to edit and handle save and cancel actions as needed.
 */
export interface FilePreviewEditing {
  /**
   * 进入编辑时读取完整文本，适用于截断预览或文件地址。
   * 省略时使用 `content` 或 `file` 中的文本；仅提供 `url` 时需提供此读取函数。
   * 返回 `undefined` 时放弃本次编辑，保持预览态。
   * Provides the full text when editing is requested, for example for a truncated
   * preview or a file URL. When omitted, text from `content` or `file` is used;
   * URL-only sources require this loader. Returning `undefined` cancels the edit
   * request and keeps the preview open.
   */
  loadContent?: (source: FilePreviewSource) => Promise<string | undefined>;
  /** Takes precedence over loadContent and supplies the draft and its editor details. */
  loadDocument?: (
    source: FilePreviewSource,
  ) => Promise<FilePreviewEditorDocument | undefined>;
  /**
   * 为 true 时，保存前弹出版本备注，确认后将备注传给 save。
   * When true, prompt for a version note before saving and pass it to save.
   */
  promptForVersionNote?: boolean;
  /**
   * 保存草稿。抛错或 reject 时保留草稿并显示失败提示；正文已保存但未创建版本时，
   * 可返回 warning 让组件退出编辑并显示提示。
   * Saves the draft. A thrown error or rejection keeps the draft and displays
   * a failure message. Return a warning to leave editing and show that the
   * file was saved without a new version.
   */
  save: (
    value: string,
    versionNote?: string,
  ) => Promise<void | { warning: 'version-not-created' }>;
  /**
   * 保存成功后触发，参数为已保存的文本；失败时不触发。
   * Called after saving succeeds, with the saved text; not called on failure.
   */
  onSave?: (value: string) => void;
  /**
   * 取消编辑或切换页签并丢弃草稿后触发，参数为丢弃的文本；保存成功时不触发。
   * Called after cancelling editing or switching tabs discards the draft;
   * receives the discarded text and is not called after a successful save.
   */
  onCancel?: (value: string) => void;
}
