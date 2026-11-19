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
    customToolbar: '自定义工具栏',
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
    hint: '模拟操作等待 2 秒。执行时可切换页签、修改或替换工具栏，再返回检查状态；取消勾选失败后可重试。',
  },
  'en-US': {
    mode: 'Action layout',
    customToolbar: 'Custom toolbar',
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
    hint: 'Demo commands wait 2 seconds. Switch tabs, edit or replace the toolbar and return to check pending state. Uncheck failure to retry.',
  },
};
