const enUSCommands = {
  get: 'Download a file',
  export: 'Export a resource as an ovpack',
  backup: 'Download an account backup',
  'session new': 'Create a session',
  'session list': 'List sessions',
  'session get': 'Inspect a session',
  'session get-session-context': 'Read session context',
  'session get-session-archive': 'Read an archived session',
  'session delete': 'Delete a session',
  'session add-message': 'Add a message to a session',
  'session add-messages': 'Add multiple messages to a session',
  'session commit': 'Commit a session',
};

const zhCNCommands = {
  get: '下载文件',
  export: '将资源导出为 ovpack',
  backup: '下载账号备份',
  'session new': '创建会话',
  'session list': '列出会话',
  'session get': '查看会话',
  'session get-session-context': '读取会话上下文',
  'session get-session-archive': '读取会话归档',
  'session delete': '删除会话',
  'session add-message': '向会话添加一条消息',
  'session add-messages': '向会话批量添加消息',
  'session commit': '提交会话',
} satisfies Record<keyof typeof enUSCommands, string>;

export const transferCommands = {
  'en-US': enUSCommands,
  'zh-CN': zhCNCommands,
};
