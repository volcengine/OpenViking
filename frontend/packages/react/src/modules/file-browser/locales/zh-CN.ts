import type { ShellLabels } from '../file-browser-labels.js';

const messages = {
  formatCharacterCount: (count) => `${count.toLocaleString('zh-CN')} 个字符`,
  empty: '选择一个文件查看内容',
  error: '目录加载失败',
  retry: '重试',
  refresh: '刷新',
  searchPlaceholder: '搜索文件 ⌘+K...',
  searchEmpty: '没有匹配的资源',
  searchFailed: '搜索失败',
  loading: '加载中…',
  searchLoading: '搜索中…',
  previewMode: '预览',
  sourceMode: '源码',
  copyPath: '复制路径',
  copyContent: '复制正文',
  download: '下载',
  history: '版本历史',
  viewSwitch: '切换视图',
  resizeTree: '调整目录树宽度',
  l0Summary: 'L0 摘要',
  l1Overview: 'L1 总览',
  summaryEmpty: '暂无生成内容',
  summaryFailed: '摘要加载失败。',
} satisfies Required<ShellLabels>;

export default messages;
