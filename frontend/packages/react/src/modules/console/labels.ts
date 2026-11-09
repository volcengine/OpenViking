import type { ConsoleLabels } from './types.js';

export function consoleLabels(locale: 'en' | 'zh', overrides?: ConsoleLabels) {
  const defaults = {
    ...(locale === 'zh'
      ? {
          conversation: 'VikingBot',
          command: '终端',
          mode: '面板模式',
          description:
            '连接你的资料、记忆与上下文，让每一份数据都成为可用的知识',
          clear: '新建会话',
          history: '命令历史',
          emptyHistory: '暂无命令历史',
          commandTitle: 'OpenViking CLI',
          commandDescription: 'CLI 直接操作上下文',
          quickStart: '快速体验：',
        }
      : {
          conversation: 'VikingBot',
          command: 'Terminal',
          mode: 'Panel mode',
          description:
            'Connect your resources, memories and context to turn your data into usable knowledge.',
          clear: 'New session',
          history: 'Command history',
          emptyHistory: 'No command history yet',
          commandTitle: 'OpenViking CLI',
          commandDescription: 'Work directly with your context through the CLI',
          quickStart: 'Quick start:',
        }),
  };
  return {
    ...defaults,
    ...overrides,
    vikingBot: {
      welcomeTitle:
        locale === 'zh' ? '欢迎使用 VikingBot' : 'Welcome to VikingBot',
      welcomeDescription: overrides?.description ?? defaults.description,
      ...(locale === 'zh'
        ? {
            placeholder: '我可以基于目录进行问答',
            send: '发送',
            copy: '复制',
            copied: '已复制',
            activityRunning: '执行中',
            activityCompleted: '已执行',
            toolInput: '输入',
            toolOutput: '输出',
            similarity: '相似度',
            toolRunning: '正在调用',
            toolCompleted: '已调用',
            toolError: '调用失败',
            stop: '停止',
            sessionHistory: '历史会话',
            newSession: '新建会话',
            emptySessions: '暂无历史会话',
            loading: '加载中…',
            loadMore: '加载更多',
            reference: '引用文件或目录',
            searchReferences: '搜索文件或目录',
            emptyReferences: '暂无匹配的文件或目录',
            removeReference: '移除引用',
            expandReference: '展开 {name}',
            collapseReference: '收起 {name}',
            attach: '添加资源',
            removeAttachment: '移除附件',
          }
        : {}),
      ...overrides?.vikingBot,
    },
  };
}
