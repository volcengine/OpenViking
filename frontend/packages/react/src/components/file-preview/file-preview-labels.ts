import type { FilePreviewLabels } from './types.js';

/** 组件内置文案，宿主可通过 `labels` 逐项覆盖。 */
export const DEFAULT_LABELS: Required<FilePreviewLabels> = {
  copyContent: 'Copy content',
  copyContentSuccess: 'Content copied',
  copyContentEmpty: 'Nothing to copy',
  copyPath: 'Copy path',
  copyPathSuccess: 'Path copied',
  openParent: 'Open directory',
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
  binaryUnsupported: 'This file cannot be previewed inline',
  fileEmpty: 'This file is empty',
  noContent: 'No content to show',
  truncated: 'Only part of the content is shown',
};
