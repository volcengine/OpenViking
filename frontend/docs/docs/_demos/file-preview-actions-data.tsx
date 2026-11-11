// Host-owned icons and workflows; none of these commands call a real service.
const icon = (path: string) => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.5"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d={path} />
  </svg>
);
export const copyIcon = icon('M7 8V4h13v13h-4M4 8h12v12H4z');
export const historyIcon = icon('M3 11a9 9 0 1 1 2 7M3 5v6h6M12 7v5l3 2');
export const deleteIcon = icon(
  'M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6',
);
export const messages = {
  'zh-CN': {
    mode: '操作布局',
    showModes: '显示模式切换',
    showEdit: '显示编辑',
    showPath: '显示复制路径',
    modes: ['无操作', '单个操作', '多个操作', '显式分组', '混合与空分组'],
    copy: '复制正文',
    history: '查看历史',
    remove: '删除文档',
    disabled: '暂不可用',
    fail: '模拟失败',
    failure: '模拟失败，请重试',
    done: '模拟完成',
    hint: '模拟操作：单个显示图标；多个 hover 展开。勾选失败后，可取消勾选再重试。',
  },
  'en-US': {
    mode: 'Action layout',
    showModes: 'Show view switch',
    showEdit: 'Show edit',
    showPath: 'Show copy path',
    modes: [
      'No actions',
      'Single action',
      'Multiple actions',
      'Explicit groups',
      'Mixed and empty groups',
    ],
    copy: 'Copy content',
    history: 'View history',
    remove: 'Delete document',
    disabled: 'Unavailable',
    fail: 'Simulate failure',
    failure: 'Simulated failure; try again',
    done: 'Simulated completion',
    hint: 'Demo commands: a single icon, or hover to open multiple actions. Uncheck failure to retry.',
  },
};
