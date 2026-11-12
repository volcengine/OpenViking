import type { ShellLabels } from '../file-browser-labels.js';

export const stackedEmpty = 'Select a file above to preview it';

const messages = {
  formatCharacterCount: (count) =>
    `${count.toLocaleString('en-US')} ${count === 1 ? 'character' : 'characters'}`,
  accessDenied: 'You do not have permission to view this node.',
  traverseOnly:
    'This directory can only be expanded to reach accessible children.',
  systemDirectory: 'No content',
  empty: 'Select a file on the left to preview it',
  error: 'Failed to load the directory',
  retry: 'Retry',
  refresh: 'Refresh',
  searchPlaceholder: 'Search files ⌘+K...',
  searchTitle: 'Search files',
  searchAction: 'Search',
  searchHint: 'Type to search for files.',
  searchEmpty: 'No matching resources',
  searchFailed: 'Failed to run the search',
  loading: 'Loading…',
  searchLoading: 'Searching…',
  formatSearchResults: (limit) => `Top ${limit} matches`,
  searchDismiss: 'Esc to close',
  searchPreview: 'Search result preview',
  searchPreviewEmpty: 'No content to preview',
  searchPreviewFailed: 'Could not load the preview',
  previewMode: 'Preview',
  sourceMode: 'Source',
  copyPath: 'Copy path',
  copyContent: 'Copy content',
  download: 'Download',
  history: 'Version history',
  importPack: 'Import .ovpack',
  exportPack: 'Export .ovpack',
  deleteFile: 'Delete document',
  deleteTitle: 'Delete document?',
  deleteMessage: (name) =>
    `You are deleting “${name}”. It will be removed from the filesystem and your connected Agent will no longer be able to read it. Delete this document?`,
  deleteFailed: 'Failed to delete the document',
  deleteConfirm: 'Delete',
  deleteCancel: 'Cancel',
  viewSwitch: 'Switch view',
  resizeTree: 'Resize the directory tree',
  l0Summary: 'L0 Summary',
  l1Overview: 'L1 Overview',
  summaryEmpty: 'No summary generated yet',
  summaryFailed: 'The summary could not be loaded.',
} satisfies Required<ShellLabels>;

export default messages;
