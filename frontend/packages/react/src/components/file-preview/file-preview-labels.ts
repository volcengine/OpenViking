import type { FilePreviewLabels } from './types.js';

/**
 * 组件内置文案，宿主可通过 `labels` 逐项覆盖。
 *
 * 只包含组件自己渲染的文案：复制路径按钮、加载与失败态、格式不支持、空文件
 * 与截断提示。其余动作按钮（复制正文、下载、预览/源码切换、编辑）由宿主在
 * `slots.actions` 里自绘，文案归宿主，这里不重复提供。
 *
 * Built-in copy, overridable field by field through `labels`.
 *
 * Only what the component renders itself: the copy-path button, loading and
 * failure states, unsupported formats, empty files and the truncation notice.
 * Every other action button (copy content, download, preview/source, edit) is
 * drawn by the host in `slots.actions` and carries its own copy.
 */
export const DEFAULT_LABELS: Required<FilePreviewLabels> = {
  copyPath: 'Copy path',
  editor: 'Edit content',
  leaveEditing: 'Cancel',
  retry: 'Retry',
  loadFailed: 'Failed to load the file',
  saveFailed: 'Failed to save',
  unsupported: 'Preview is not supported for this format',
  loading: 'Loading…',
  download: 'Download',
  fileEmpty: 'This file is empty',
  noContent: 'No content to show',
  truncated: 'Only part of the content is shown',
};
