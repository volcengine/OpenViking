import type { FilePreviewLabels } from '../types.js';

const messages = {
  showHiddenPath: 'Show hidden path levels',
  moreActions: 'More actions',
  actionFailed: 'The action failed',
  copyPath: 'Copy path',
  copyContent: 'Copy content',
  copied: 'Copied',
  copyFailed: 'Could not copy',
  previewMode: 'Preview',
  sourceMode: 'Source',
  viewSwitch: 'View mode',
  edit: 'Edit',
  editFile: 'Edit file',
  save: 'Save',
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
