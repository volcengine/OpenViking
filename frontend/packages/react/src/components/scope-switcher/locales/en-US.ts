import type { ScopeSwitcherLabels } from '../types.js';

const messages = {
  loading: 'Loading…',
  searchPlaceholder: 'Search',
  empty: 'No options',
  noMatch: 'No matches',
  error: 'Unable to load options',
} satisfies Required<ScopeSwitcherLabels>;

export default messages;
