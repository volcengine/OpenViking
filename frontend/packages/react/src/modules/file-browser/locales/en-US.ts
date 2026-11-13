import type { ShellLabels } from '../file-browser-labels.js';

const messages = {
  formatCharacterCount: (count) =>
    `${count.toLocaleString('en-US')} ${count === 1 ? 'character' : 'characters'}`,
  accessDenied: 'You do not have permission to view this node.',
  traverseOnly:
    'This directory can only be expanded to reach accessible children.',
  systemDirectory: 'Select a file in this directory to preview its content.',
  empty: 'Select a file on the left to preview it',
  error: 'Failed to load the directory',
  retry: 'Retry',
  refresh: 'Refresh',
  searchPlaceholder: 'Search files and content',
  searchTitle: 'Search files and content',
  searchEmptyTitle: 'Start searching',
  searchHint: 'Enter a keyword to search context files or directories',
  searchEmpty: 'No matching resources',
  searchFailed: 'Failed to run the search',
  searchModePlaceholders: {
    find: 'Search files or directories',
    search: 'Enter a question that needs conversation context',
    grep: 'Enter a regular expression or exact text',
    glob: 'Enter a file pattern, e.g. **/*.md',
  },
  searchModeHints: {
    find: 'Semantic retrieval by vector similarity',
    search: 'Semantic retrieval with context',
    grep: 'Full-text retrieval',
    glob: 'Retrieve file names by pattern',
  },
  searchAdvanced: 'Advanced',
  searchClear: 'Clear',
  searchScopes: 'Directory',
  searchPickScope: 'Choose a directory',
  searchScopesUnsupported: 'The current scope cannot be filtered by directory',
  searchLevels: 'Content',
  searchLevelLabels: ['L0 Summary', 'L1 Overview', 'L2 Full text'],
  searchTime: 'Time',
  searchCreatedAt: 'By creation time',
  searchUpdatedAt: 'By update time',
  searchSince: 'Start date',
  searchUntil: 'End date',
  searchIgnoreCase: 'Ignore case',
  searchFilterUnsupported: 'This filter is unavailable for the current mode',
  searchResultCount: (count) =>
    `${count} ${count === 1 ? 'result' : 'results'}`,
  searchVia: 'Vector retrieval',
  searchSwitchMode: 'Switch',
  searchAdvancedLabel: 'Advanced filters',
  loading: 'Loading…',
  searchDismiss: 'Esc to close',
  searchLoading: 'Searching…',
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

export const stackedEmpty = 'Select a file above to preview it';

export const renderErrors = {
  browser: 'File browser could not be displayed.',
  tree: 'File tree could not be displayed.',
  preview: 'File preview failed.',
  search: 'Search results could not be displayed.',
};
