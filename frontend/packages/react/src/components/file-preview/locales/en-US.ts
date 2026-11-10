import type { FilePreviewLabels } from '../types.js';

const messages = {
  showHiddenPath: 'Show hidden path levels',
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
} satisfies Required<FilePreviewLabels>;

export default messages;
