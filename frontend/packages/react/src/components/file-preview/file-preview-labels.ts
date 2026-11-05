import type { FilePreviewFileTab, FilePreviewLabels } from './types.js';

/** 组件内置文案，宿主可通过面板或页签级 `labels` 覆盖。 */
export const DEFAULT_LABELS: Required<FilePreviewLabels> = {
  copyContent: 'Copy content',
  copyContentSuccess: 'Content copied',
  copyContentEmpty: 'Nothing to copy',
  copyPath: 'Copy path',
  copyPathSuccess: 'Path copied',
  openParent: 'Open parent directory',
  edit: 'Edit',
  save: 'Save',
  cancel: 'Cancel',
  preview: 'Preview',
  source: 'Source',
  retry: 'Retry',
  loadFailed: 'Failed to load the file',
  saveFailed: 'Failed to save',
  unsupported: 'Preview is not supported for this format',
  loading: 'Loading…',
  download: 'Download',
  binaryUnsupported: 'Binary preview requires FileViewer',
};

/** 从文件页签的 `source` 里取出可复制的纯文本；拿不到时返回 `undefined`。 */
export function readCopyableText(tab: FilePreviewFileTab): string | undefined {
  const { content } = tab.source;
  return typeof content === 'string' && content.length > 0
    ? content
    : undefined;
}
