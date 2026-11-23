import type { ScopeSwitcherLabels } from '../types.js';

const messages = {
  loading: '加载中…',
  searchPlaceholder: '搜索',
  empty: '暂无选项',
  noMatch: '没有匹配项',
  error: '选项加载失败',
} satisfies Required<ScopeSwitcherLabels>;

export default messages;
