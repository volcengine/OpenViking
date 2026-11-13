import type { FilePreviewLabels } from '../types.js';

const messages = {
  previewImage: 'Enlarge image',
  closePreview: 'Close preview',
  showHiddenPath: 'Show hidden path levels',
  moreActions: 'More actions',
  actionFailed: 'The action failed',
  copyPath: 'Copy path',
  copied: 'Copied',
  copyFailed: 'Could not copy',
  previewMode: 'Preview',
  sourceMode: 'Source',
  viewSwitch: 'View mode',
  edit: 'Edit',
  editFile: 'Edit file',
  save: 'Save',
  editor: 'Edit content',
  editorLoadFailed: 'Failed to load the editor',
  highlightFailed: 'Failed to load syntax highlighting',
  leaveEditing: 'Cancel',
  retry: 'Retry',
  loadFailed: 'Failed to load the file',
  saveFailed: 'Failed to save',
  unsupported: 'Preview is not supported for this format',
  loading: 'Loading…',
  fileEmpty: 'This file is empty',
  noContent: 'No content to show',
  truncated: 'Only part of the content is shown',
} satisfies Required<FilePreviewLabels>;

export default messages;

export const renderErrors = {
  preview: 'File preview failed.',
  content: 'Tab content could not be displayed.',
  editor: 'File editor could not be displayed.',
};
