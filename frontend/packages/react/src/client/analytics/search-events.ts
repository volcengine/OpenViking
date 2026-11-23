import { failed, search } from './fields.js';

export const searchEvents = {
  file_browser_search_submit: {
    zh: '去抖后的有效查询开始请求，每次查询变化是新操作。',
    en: 'A nonempty debounced query starts a request; each query change is a new operation.',
    fields: search,
  },
  file_browser_search_success: {
    zh: '当前搜索返回结果，包括零条结果。',
    en: 'The current search returns, including zero results.',
    fields: { ...search, duration_ms: 'number', result_count: 'number' },
  },
  file_browser_search_fail: {
    zh: '当前搜索失败；取消或过期请求不计失败。',
    en: 'The current search fails; cancelled and superseded requests are excluded.',
    fields: { ...search, duration_ms: 'number', error_kind: failed.error_kind },
  },
} as const;
