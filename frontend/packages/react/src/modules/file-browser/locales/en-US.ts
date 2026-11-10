import type { ShellLabels } from '../file-browser-labels.js';

const messages = {
  formatCharacterCount: (count) =>
    `${count.toLocaleString('en-US')} ${count === 1 ? 'character' : 'characters'}`,
  empty: 'Select a file to preview',
  error: 'Failed to load the directory',
  retry: 'Retry',
  refresh: 'Refresh',
  searchPlaceholder: 'Search files ⌘+K...',
  searchEmpty: 'No matching resources',
  searchFailed: 'Failed to run the search',
  loading: 'Loading…',
  searchLoading: 'Searching…',
  previewMode: 'Preview',
  sourceMode: 'Source',
  copyPath: 'Copy path',
  copyContent: 'Copy content',
  download: 'Download',
  history: 'Version history',
  viewSwitch: 'Switch view',
  resizeTree: 'Resize the directory tree',
  l0Summary: 'L0 Summary',
  l1Overview: 'L1 Overview',
  summaryEmpty: 'No summary generated yet',
  summaryFailed: 'The summary could not be loaded.',
} satisfies Required<ShellLabels>;

export default messages;
